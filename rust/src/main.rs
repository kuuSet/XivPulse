use std::collections::HashMap;
use std::env;
use std::io::{self, BufWriter, Write};
use std::time::Duration;

use anyhow::{Context, Result, bail};
use chrono::DateTime;
use reqwest::blocking::Client;
use roxmltree::{Document, Node};
use serde::Serialize;

const RSS_BASE_URL: &str = "https://rss.arxiv.org/rss";
const DEFAULT_CATEGORIES: &str = "cs.RO,cs.AI,cs.CV,cs.LG";
const DEFAULT_LIMIT: usize = 2_000;
const MAX_RESPONSE_BYTES: usize = 20 * 1024 * 1024;

#[derive(Clone, Debug, Serialize)]
struct Paper {
    arxiv_id: String,
    version: u32,
    title: String,
    #[serde(rename = "abstract")]
    abstract_text: String,
    authors: Vec<String>,
    affiliations: Vec<String>,
    categories: Vec<String>,
    primary_category: Option<String>,
    comment: Option<String>,
    journal_ref: Option<String>,
    doi: Option<String>,
    abs_url: String,
    html_url: String,
    pdf_url: String,
    published_at: Option<String>,
    updated_at: Option<String>,
    source: &'static str,
}

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    if args.next().as_deref() != Some("fetch") || args.next().is_some() {
        bail!("usage: xivpulse fetch");
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

    let url = format!("{RSS_BASE_URL}/{}", categories.join("+"));
    let client = Client::builder()
        .user_agent("XivPulse/0.1 (https://github.com/kuuSet/XivPulse)")
        .timeout(Duration::from_secs(45))
        .build()?;
    let response = client
        .get(&url)
        .send()
        .context("arXiv RSS request failed")?
        .error_for_status()
        .context("arXiv RSS returned an error status")?;
    if response
        .content_length()
        .is_some_and(|size| size > MAX_RESPONSE_BYTES as u64)
    {
        bail!("arXiv RSS response exceeded 20 MiB");
    }
    let body = response
        .bytes()
        .context("failed to read arXiv RSS response")?;
    if body.len() > MAX_RESPONSE_BYTES {
        bail!("arXiv RSS response exceeded 20 MiB");
    }
    let body = std::str::from_utf8(&body).context("arXiv RSS response is not UTF-8")?;
    let papers = parse_feed(body)?;

    let stdout = io::stdout();
    let mut output = BufWriter::new(stdout.lock());
    for paper in papers.iter().take(limit) {
        serde_json::to_writer(&mut output, paper)?;
        writeln!(output)?;
    }
    output.flush()?;
    eprintln!(
        "categories={} fetched={} emitted={}",
        categories.join(","),
        papers.len(),
        papers.len().min(limit)
    );
    Ok(())
}

fn parse_categories(value: &str) -> Result<Vec<String>> {
    let mut categories = Vec::new();
    for value in value.split(',') {
        let category = value.trim();
        let valid = category.len() >= 2
            && category
                .chars()
                .next()
                .is_some_and(|character| character.is_ascii_alphabetic())
            && category
                .chars()
                .all(|character| character.is_ascii_alphanumeric() || ".-".contains(character));
        if !valid {
            bail!("XIVPULSE_ARXIV_CATEGORIES contains an invalid category");
        }
        if !categories.iter().any(|current| current == category) {
            categories.push(category.to_owned());
        }
    }
    if categories.is_empty() {
        bail!("XIVPULSE_ARXIV_CATEGORIES contains an invalid category");
    }
    Ok(categories)
}

fn parse_feed(xml: &str) -> Result<Vec<Paper>> {
    let document = Document::parse(xml).context("invalid arXiv RSS response")?;
    if !document
        .root_element()
        .tag_name()
        .name()
        .eq_ignore_ascii_case("rss")
    {
        bail!("arXiv response is not an RSS feed");
    }

    let mut papers = Vec::<Paper>::new();
    let mut positions = HashMap::<String, usize>::new();
    for item in document
        .descendants()
        .filter(|node| node.is_element() && node.tag_name().name() == "item")
    {
        let paper = parse_item(item)?;
        if let Some(position) = positions.get(&paper.arxiv_id).copied() {
            let current = &papers[position];
            let newer = paper.version > current.version
                || (paper.version == current.version && paper.updated_at > current.updated_at);
            if newer {
                papers[position] = paper;
            }
        } else {
            positions.insert(paper.arxiv_id.clone(), papers.len());
            papers.push(paper);
        }
    }
    Ok(papers)
}

fn parse_item(item: Node<'_, '_>) -> Result<Paper> {
    let raw_id = child_text(item, &["guid"])
        .or_else(|| child_text(item, &["link"]))
        .or_else(|| child_text(item, &["description"]))
        .context("arXiv RSS item is missing an identifier")?;
    let (arxiv_id, version) = parse_arxiv_id(&raw_id)?;
    let title = child_text(item, &["title"]).context("arXiv RSS item is missing a title")?;
    let description = child_text(item, &["description", "summary"]).unwrap_or_default();
    let abstract_text = html_to_text(&description);
    let authors = split_authors(&child_text(item, &["creator", "author"]).unwrap_or_default());
    let categories = unique(
        item.children()
            .filter(|node| {
                node.is_element()
                    && matches!(node.tag_name().name(), "category" | "primary_category")
            })
            .filter_map(|node| node.text().map(clean_text))
            .filter(|value| !value.is_empty())
            .collect(),
    );
    let primary_category = categories.first().cloned();
    let published_at = child_text(item, &["pubDate", "date", "published"])
        .map(|value| normalize_timestamp(&value))
        .transpose()?;
    let updated_at = child_text(item, &["updated", "pubDate", "date"])
        .map(|value| normalize_timestamp(&value))
        .transpose()?;

    let versioned_id = format!("{arxiv_id}v{version}");
    let link = child_text(item, &["link"]).unwrap_or_default();
    let abs_url = if link.contains("/abs/") {
        link
    } else {
        format!("https://arxiv.org/abs/{versioned_id}")
    };

    Ok(Paper {
        arxiv_id,
        version,
        title,
        abstract_text,
        authors,
        affiliations: Vec::new(),
        categories,
        primary_category,
        comment: None,
        journal_ref: optional_child_text(item, &["journal_reference"]),
        doi: optional_child_text(item, &["DOI", "doi"]),
        abs_url,
        html_url: format!("https://arxiv.org/html/{versioned_id}"),
        pdf_url: format!("https://arxiv.org/pdf/{versioned_id}"),
        published_at,
        updated_at,
        source: "rss",
    })
}

fn child_text(node: Node<'_, '_>, names: &[&str]) -> Option<String> {
    node.children()
        .find(|child| {
            child.is_element()
                && names
                    .iter()
                    .any(|name| child.tag_name().name().eq_ignore_ascii_case(name))
        })
        .and_then(|child| child.text())
        .map(clean_text)
}

fn optional_child_text(node: Node<'_, '_>, names: &[&str]) -> Option<String> {
    child_text(node, names).filter(|value| !value.is_empty())
}

fn clean_text(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn html_to_text(value: &str) -> String {
    let mut text = String::with_capacity(value.len());
    let mut in_tag = false;
    for character in value.chars() {
        match character {
            '<' => {
                in_tag = true;
                text.push(' ');
            }
            '>' => in_tag = false,
            _ if !in_tag => text.push(character),
            _ => {}
        }
    }
    clean_text(&html_escape::decode_html_entities(&text))
}

fn split_authors(value: &str) -> Vec<String> {
    value
        .replace(';', ",")
        .replace(" and ", ",")
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect()
}

fn unique(values: Vec<String>) -> Vec<String> {
    let mut unique = Vec::new();
    for value in values {
        if !unique.contains(&value) {
            unique.push(value);
        }
    }
    unique
}

fn normalize_timestamp(value: &str) -> Result<String> {
    DateTime::parse_from_rfc2822(value)
        .or_else(|_| DateTime::parse_from_rfc3339(value))
        .map(|value| value.to_rfc3339())
        .with_context(|| format!("invalid arXiv RSS timestamp: {value}"))
}

fn parse_arxiv_id(value: &str) -> Result<(String, u32)> {
    let mut raw = value.trim();
    if let Some((_, suffix)) = raw.split_once("/abs/") {
        raw = suffix;
    } else if let Some((_, suffix)) = raw.split_once("oai:arXiv.org:") {
        raw = suffix;
    } else if let Some((_, suffix)) = raw.split_once("arXiv:") {
        raw = suffix;
    }
    raw = raw
        .split(['?', '#', ' '])
        .next()
        .unwrap_or(raw)
        .trim_end_matches('/');
    raw = raw.strip_suffix(".pdf").unwrap_or(raw);
    if raw.is_empty() {
        bail!("arXiv RSS item contains an empty id");
    }

    let (base_id, version) = if let Some(index) = raw.rfind('v') {
        let suffix = &raw[index + 1..];
        if !suffix.is_empty() && suffix.chars().all(|character| character.is_ascii_digit()) {
            (
                &raw[..index],
                suffix.parse().context("arXiv version is invalid")?,
            )
        } else {
            (raw, 1)
        }
    } else {
        (raw, 1)
    };
    let valid = !base_id.is_empty()
        && base_id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || ".-/".contains(character));
    if !valid || version == 0 {
        bail!("arXiv RSS item contains an invalid id: {value}");
    }
    Ok((base_id.to_owned(), version))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn categories_are_validated_and_deduplicated() {
        assert_eq!(
            parse_categories("cs.RO, cs.AI,cs.RO").unwrap(),
            ["cs.RO", "cs.AI"]
        );
        assert!(parse_categories("cs.RO,../bad").is_err());
    }

    #[test]
    fn rss_feed_is_normalized_deduplicated_and_ordered() {
        let papers = parse_feed(include_str!("../tests/fixtures/arxiv_rss.xml")).unwrap();

        assert_eq!(papers.len(), 2);
        assert_eq!(papers[0].arxiv_id, "2608.00001");
        assert_eq!(papers[0].version, 2);
        assert_eq!(papers[0].title, "A newer robot paper");
        assert_eq!(papers[0].authors, ["Ada Researcher", "Grace Scientist"]);
        assert_eq!(papers[0].primary_category.as_deref(), Some("cs.RO"));
        assert_eq!(papers[0].source, "rss");
        assert!(papers[0].abstract_text.contains("Announce Type: replace"));
        let output = serde_json::to_value(&papers[0]).unwrap();
        assert!(output.get("abstract").is_some());
        assert!(output.get("abstract_text").is_none());
        assert_eq!(papers[1].arxiv_id, "2608.00002");
    }

    #[test]
    fn empty_weekend_feed_is_valid() {
        let papers = parse_feed(
            r#"<?xml version="1.0"?><rss version="2.0"><channel><title>empty</title></channel></rss>"#,
        )
        .unwrap();
        assert!(papers.is_empty());
    }
}
