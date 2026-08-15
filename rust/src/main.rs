use std::collections::BTreeMap;
use std::env;
use std::io::{self, BufWriter, Write};
use std::time::Duration;

use anyhow::{Context, Result, bail};
use chrono::NaiveDate;
use reqwest::blocking::Client;
use roxmltree::{Document, Node};
use serde::Serialize;

const API_URL: &str = "https://export.arxiv.org/api/query";
const DEFAULT_CATEGORIES: &str = "cs.RO,cs.AI,cs.CV,cs.LG";
const DEFAULT_LIMIT: usize = 2_000;

#[derive(Clone, Debug, Serialize)]
struct Paper {
    arxiv_id: String,
    version: u32,
    title: String,
    authors: Vec<String>,
    categories: Vec<String>,
    primary_category: Option<String>,
    abstract_text: String,
    published_at: String,
    updated_at: String,
    abs_url: String,
    pdf_url: String,
}

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    if args.next().as_deref() != Some("fetch") {
        bail!("usage: xivpulse fetch YYYY-MM-DD");
    }
    let date = args
        .next()
        .context("missing date; usage: xivpulse fetch YYYY-MM-DD")?
        .parse::<NaiveDate>()
        .context("date must use YYYY-MM-DD")?;
    if args.next().is_some() {
        bail!("usage: xivpulse fetch YYYY-MM-DD");
    }

    let categories = parse_categories(
        &env::var("XIVPULSE_ARXIV_CATEGORIES").unwrap_or_else(|_| DEFAULT_CATEGORIES.into()),
    )?;
    let limit = env::var("XIVPULSE_FETCH_LIMIT")
        .ok()
        .map(|value| value.parse::<usize>())
        .transpose()
        .context("XIVPULSE_FETCH_LIMIT must be an integer")?
        .unwrap_or(DEFAULT_LIMIT);
    if !(1..=2_000).contains(&limit) {
        bail!("XIVPULSE_FETCH_LIMIT must be between 1 and 2000");
    }

    let query = build_query(date, &categories);
    let client = Client::builder()
        .user_agent("XivPulse/0.1 (https://github.com/kuuSet/XivPulse)")
        .timeout(Duration::from_secs(45))
        .build()?;
    let response = client
        .get(API_URL)
        .query(&[
            ("search_query", query),
            ("start", "0".to_owned()),
            ("max_results", limit.to_string()),
            ("sortBy", "submittedDate".to_owned()),
            ("sortOrder", "ascending".to_owned()),
        ])
        .send()
        .context("arXiv request failed")?
        .error_for_status()
        .context("arXiv returned an error status")?;
    let body = response.text().context("failed to read arXiv response")?;
    let (total, papers) = parse_feed(&body)?;
    if total > limit {
        bail!("arXiv reports {total} papers for {date}, exceeding the configured limit {limit}");
    }
    for paper in &papers {
        if paper.published_at.get(..10) != Some(&date.to_string()) {
            bail!(
                "arXiv returned {} with published date {} outside requested date {date}",
                paper.arxiv_id,
                paper.published_at
            );
        }
    }

    let stdout = io::stdout();
    let mut output = BufWriter::new(stdout.lock());
    for paper in &papers {
        serde_json::to_writer(&mut output, paper)?;
        writeln!(output)?;
    }
    output.flush()?;
    eprintln!(
        "date={date} categories={} total={total} emitted={}",
        categories.join(","),
        papers.len()
    );
    Ok(())
}

fn parse_categories(value: &str) -> Result<Vec<String>> {
    let categories: Vec<String> = value
        .split(',')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .map(str::to_owned)
        .collect();
    if categories.is_empty()
        || categories.iter().any(|item| {
            !item.contains('.')
                || !item
                    .chars()
                    .all(|character| character.is_ascii_alphanumeric() || ".-".contains(character))
        })
    {
        bail!("XIVPULSE_ARXIV_CATEGORIES contains an invalid category");
    }
    Ok(categories)
}

fn build_query(date: NaiveDate, categories: &[String]) -> String {
    let compact = date.format("%Y%m%d");
    let category_query = categories
        .iter()
        .map(|category| format!("cat:{category}"))
        .collect::<Vec<_>>()
        .join(" OR ");
    format!("({category_query}) AND submittedDate:[{compact}0000 TO {compact}2359]")
}

fn parse_feed(xml: &str) -> Result<(usize, Vec<Paper>)> {
    let document = Document::parse(xml).context("invalid arXiv Atom response")?;
    let total = document
        .descendants()
        .find(|node| node.is_element() && node.tag_name().name() == "totalResults")
        .and_then(|node| node.text())
        .context("arXiv response is missing totalResults")?
        .trim()
        .parse::<usize>()
        .context("arXiv totalResults is invalid")?;

    let mut selected = BTreeMap::<String, Paper>::new();
    for entry in document
        .descendants()
        .filter(|node| node.is_element() && node.tag_name().name() == "entry")
    {
        let paper = parse_entry(entry)?;
        let replace = selected.get(&paper.arxiv_id).is_none_or(|current| {
            paper.version > current.version
                || (paper.version == current.version && paper.updated_at > current.updated_at)
        });
        if replace {
            selected.insert(paper.arxiv_id.clone(), paper);
        }
    }

    let mut papers: Vec<Paper> = selected.into_values().collect();
    papers.sort_by(|left, right| {
        left.published_at
            .cmp(&right.published_at)
            .then_with(|| left.arxiv_id.cmp(&right.arxiv_id))
            .then_with(|| left.version.cmp(&right.version))
    });
    Ok((total, papers))
}

fn parse_entry(entry: Node<'_, '_>) -> Result<Paper> {
    let raw_id = child_text(entry, "id").context("arXiv entry is missing id")?;
    let (arxiv_id, version) = parse_arxiv_id(&raw_id)?;
    let title = child_text(entry, "title").context("arXiv entry is missing title")?;
    let abstract_text = child_text(entry, "summary").unwrap_or_default();
    let published_at =
        child_text(entry, "published").context("arXiv entry is missing published timestamp")?;
    let updated_at = child_text(entry, "updated").unwrap_or_else(|| published_at.clone());
    let authors = entry
        .children()
        .filter(|node| node.is_element() && node.tag_name().name() == "author")
        .filter_map(|node| child_text(node, "name"))
        .collect();
    let categories: Vec<String> = entry
        .children()
        .filter(|node| node.is_element() && node.tag_name().name() == "category")
        .filter_map(|node| node.attribute("term").map(str::to_owned))
        .collect();
    let primary_category = entry
        .children()
        .find(|node| node.is_element() && node.tag_name().name() == "primary_category")
        .and_then(|node| node.attribute("term"))
        .map(str::to_owned)
        .or_else(|| categories.first().cloned());

    let mut abs_url = String::new();
    let mut pdf_url = String::new();
    for link in entry
        .children()
        .filter(|node| node.is_element() && node.tag_name().name() == "link")
    {
        let Some(href) = link.attribute("href") else {
            continue;
        };
        if link.attribute("rel") == Some("alternate") {
            abs_url = href.to_owned();
        }
        if link.attribute("type") == Some("application/pdf")
            || link.attribute("title") == Some("pdf")
        {
            pdf_url = href.to_owned();
        }
    }
    if abs_url.is_empty() {
        abs_url = format!("https://arxiv.org/abs/{arxiv_id}v{version}");
    }
    if pdf_url.is_empty() {
        pdf_url = format!("https://arxiv.org/pdf/{arxiv_id}v{version}");
    }

    Ok(Paper {
        arxiv_id,
        version,
        title,
        authors,
        categories,
        primary_category,
        abstract_text,
        published_at,
        updated_at,
        abs_url,
        pdf_url,
    })
}

fn child_text(node: Node<'_, '_>, name: &str) -> Option<String> {
    node.children()
        .find(|child| child.is_element() && child.tag_name().name() == name)
        .and_then(|child| child.text())
        .map(clean_text)
}

fn clean_text(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn parse_arxiv_id(value: &str) -> Result<(String, u32)> {
    let raw = value
        .split("/abs/")
        .nth(1)
        .unwrap_or(value)
        .split(['?', '#'])
        .next()
        .unwrap_or(value)
        .trim();
    if raw.is_empty() {
        bail!("arXiv entry contains an empty id");
    }
    if let Some(index) = raw.rfind('v') {
        let suffix = &raw[index + 1..];
        if !suffix.is_empty() && suffix.chars().all(|character| character.is_ascii_digit()) {
            return Ok((
                raw[..index].to_owned(),
                suffix.parse().context("arXiv version is invalid")?,
            ));
        }
    }
    Ok((raw.to_owned(), 1))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn date_query_uses_the_complete_utc_day() {
        let date = NaiveDate::from_ymd_opt(2026, 8, 13).unwrap();
        assert_eq!(
            build_query(date, &["cs.RO".into(), "cs.AI".into()]),
            "(cat:cs.RO OR cat:cs.AI) AND submittedDate:[202608130000 TO 202608132359]"
        );
    }

    #[test]
    fn atom_feed_is_normalized_deduplicated_and_sorted() {
        let (total, papers) = parse_feed(include_str!("../tests/fixtures/arxiv_atom.xml")).unwrap();

        assert_eq!(total, 3);
        assert_eq!(papers.len(), 2);
        assert_eq!(papers[0].arxiv_id, "2608.00001");
        assert_eq!(papers[0].version, 2);
        assert_eq!(papers[0].title, "A newer robot paper");
        assert_eq!(papers[0].authors, ["Ada Researcher"]);
        assert_eq!(papers[0].primary_category.as_deref(), Some("cs.RO"));
        assert_eq!(papers[1].arxiv_id, "2608.00002");
    }
}
