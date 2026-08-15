"""Built-in profile, settings, and one-time default-data migrations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .services.prompt_catalog import DEFAULT_PROMPT_TEMPLATE_ID, prompt_template

DEFAULT_PROFILE_NAME = "具身智能研究"
DEFAULT_TOPICS = (
    {
        "name": "具身智能与通用机器人",
        "aliases_json": [
            "Embodied AI",
            "Embodied Intelligence",
            "Generalist Robot",
        ],
        "query": (
            'all:"embodied intelligence" OR all:"generalist robot" OR all:"robot foundation model"'
        ),
        "arxiv_categories_json": ["cs.RO", "cs.AI"],
        "keywords_json": [
            "embodied ai",
            "embodied intelligence",
            "generalist robot",
            "generalist robots",
            "robot foundation model",
            "robot foundation models",
        ],
        "context_keywords_json": [],
        "negative_keywords_json": [
            "spatial reasoning",
            "spatial grounding",
            "3d scene understanding",
        ],
        "weight": 1.35,
    },
    {
        "name": "视觉-语言-动作模型",
        "aliases_json": ["VLA", "Vision-Language-Action"],
        "query": 'all:"vision language action" OR all:VLA OR all:"multimodal robot"',
        "arxiv_categories_json": ["cs.RO", "cs.CV", "cs.AI"],
        "keywords_json": [
            "vision-language-action",
            "vision language action",
            "VLA",
            "multimodal robot",
        ],
        "context_keywords_json": [],
        "negative_keywords_json": ["vehicle-to-load"],
        "weight": 1.5,
    },
    {
        "name": "机器人学习与强化学习",
        "aliases_json": ["Robot Learning", "Reinforcement Learning for Robotics"],
        "query": 'all:"robot learning" OR all:"reinforcement learning" OR all:imitation',
        "arxiv_categories_json": ["cs.RO", "cs.LG", "cs.AI"],
        "keywords_json": [
            "robot learning",
            "reinforcement learning",
            "imitation learning",
            "behavior cloning",
            "policy learning",
        ],
        "context_keywords_json": [
            "robot",
            "robots",
            "robotic",
            "robotics",
            "humanoid",
            "humanoids",
            "manipulation",
            "locomotion",
            "real robot",
            "autonomous driving",
            "autonomous vehicle",
            "autonomous vehicles",
            "embodied",
        ],
        "negative_keywords_json": [],
        "weight": 1.2,
    },
    {
        "name": "世界模型与规划",
        "aliases_json": ["World Models and Planning", "Robot World Models"],
        "query": ('all:"world model" OR all:"model-based planning" OR all:"predictive control"'),
        "arxiv_categories_json": ["cs.RO", "cs.AI", "cs.LG"],
        "keywords_json": [
            "world model",
            "world models",
            "model-based planning",
            "predictive control",
            "latent dynamics",
        ],
        "context_keywords_json": [
            "robot",
            "robots",
            "robotic",
            "robotics",
            "autonomous driving",
            "autonomous vehicle",
            "autonomous vehicles",
            "drone",
            "drones",
            "manipulation",
            "mobile robot",
            "mobile robots",
        ],
        "negative_keywords_json": [],
        "weight": 1.15,
    },
    {
        "name": "机器人操作与灵巧手",
        "aliases_json": ["Robot Manipulation", "Dexterous Manipulation"],
        "query": "all:manipulation OR all:dexterous OR all:grasping OR all:bimanual",
        "arxiv_categories_json": ["cs.RO"],
        "keywords_json": [
            "robot manipulation",
            "dexterous manipulation",
            "grasping",
            "bimanual",
            "tactile",
        ],
        "context_keywords_json": [
            "robot",
            "robots",
            "robotic",
            "robotics",
            "manipulation",
            "grasp",
            "grasping",
            "dexterous",
        ],
        "negative_keywords_json": [],
        "weight": 1.1,
    },
    {
        "name": "导航与空间智能",
        "aliases_json": ["Robot Navigation", "Spatial Intelligence"],
        "query": (
            'all:"robot navigation" OR all:"spatial intelligence" OR all:"visual navigation"'
        ),
        "arxiv_categories_json": ["cs.RO", "cs.CV", "cs.AI"],
        "keywords_json": [
            "robot navigation",
            "visual navigation",
            "spatial intelligence",
            "SLAM",
            "3D scene understanding",
        ],
        "context_keywords_json": [
            "robot",
            "robots",
            "robotic",
            "robotics",
            "autonomous driving",
            "autonomous vehicle",
            "autonomous vehicles",
            "mobile robot",
            "mobile robots",
            "slam",
            "indoor navigation",
            "social navigation",
        ],
        "negative_keywords_json": [
            "camera motion",
            "video generation",
            "scene editing",
            "question answering",
            "dense captioning",
            "indoor videos",
        ],
        "weight": 1.0,
    },
)

DEFAULT_SETTINGS = {
    "schedule_time": "13:30",
    "timezone": "Asia/Shanghai",
    "max_results": 1000,
    "screen_top_n": 30,
    "screen_batch_size": 30,
    "semantic_recall_mode": "high",
    "deep_analysis_top_n": 5,
    "deep_relevance_threshold": 90,
    "retention_days": 365,
    "analysis_budget_mode": "balanced",
    "daily_ai_synthesis_enabled": True,
    "analysis_prompt_template_id": DEFAULT_PROMPT_TEMPLATE_ID,
    "screening_prompt": prompt_template(DEFAULT_PROMPT_TEMPLATE_ID).screening_prompt,
    "deep_prompt": prompt_template(DEFAULT_PROMPT_TEMPLATE_ID).deep_prompt,
    "custom_method_terms": [],
    "dismissed_method_candidates": [],
    "method_candidate_window_days": 30,
    "method_candidate_min_papers": 2,
    "email_enabled": False,
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "smtp_username": "",
    "smtp_password": "",
    "smtp_from": "",
    "email_recipients": [],
    "public_base_url": "",
}

_CONTEXT_ANCHOR_MIGRATION_KEY = "migration.direction_context_defaults"
_CONTEXT_ANCHOR_MIGRATION_VERSION = "v2-without-generic-control-agent"
_COST_PRESET_MIGRATION_KEY = "migration.conservative_daily_cost_preset"
_COST_PRESET_MIGRATION_VERSION = "v1-30-screen-5-deep-90-threshold"
_DIRECTION_RULE_MIGRATION_KEY = "migration.precise_default_direction_rules"
_DIRECTION_RULE_MIGRATION_VERSION = "v4-navigation-context"
_LEGACY_DIRECTION_RULE_MIGRATION_VERSIONS = {
    "v1-conservative-robotics-context",
    "v2-conservative-robotics-context",
    "v3-explicit-word-forms",
}
_LEGACY_CONTEXT_KEYWORDS = {
    "机器人学习与强化学习": [
        "robot",
        "robotic",
        "humanoid",
        "manipulation",
        "locomotion",
        "navigation",
        "autonomous driving",
        "autonomous vehicle",
        "embodied",
        "control",
    ],
    "世界模型与规划": [
        "robot",
        "robotic",
        "embodied",
        "autonomous driving",
        "autonomous vehicle",
        "drone",
        "navigation",
        "manipulation",
        "control",
        "agent",
    ],
}

_LEGACY_DIRECTION_RULES = {
    "具身智能与通用机器人": {
        "keywords": [
            "embodied ai",
            "embodied intelligence",
            "generalist robot",
            "robot foundation model",
        ],
        "context_keywords": [],
        "negative_keywords": [],
    },
    "视觉-语言-动作模型": {
        "keywords": [
            "vision-language-action",
            "vision language action",
            "VLA",
            "multimodal robot",
        ],
        "context_keywords": [],
        "negative_keywords": ["vehicle-to-load"],
    },
    "机器人学习与强化学习": {
        "keywords": [
            "robot learning",
            "reinforcement learning",
            "imitation learning",
            "behavior cloning",
            "policy learning",
        ],
        "context_keywords": [
            "robot",
            "robotic",
            "humanoid",
            "manipulation",
            "locomotion",
            "navigation",
            "autonomous driving",
            "autonomous vehicle",
            "embodied",
        ],
        "negative_keywords": [],
    },
    "世界模型与规划": {
        "keywords": [
            "world model",
            "model-based planning",
            "predictive control",
            "latent dynamics",
        ],
        "context_keywords": [
            "robot",
            "robotic",
            "embodied",
            "autonomous driving",
            "autonomous vehicle",
            "drone",
            "navigation",
            "manipulation",
        ],
        "negative_keywords": [],
    },
    "机器人操作与灵巧手": {
        "keywords": [
            "robot manipulation",
            "dexterous manipulation",
            "grasping",
            "bimanual",
            "tactile",
        ],
        "context_keywords": [],
        "negative_keywords": [],
    },
    "导航与空间智能": {
        "keywords": [
            "robot navigation",
            "visual navigation",
            "spatial intelligence",
            "SLAM",
            "3D scene understanding",
        ],
        "context_keywords": [],
        "negative_keywords": [],
    },
}

_V2_DIRECTION_RULES = {
    "具身智能与通用机器人": {
        "keywords": [
            "embodied ai",
            "embodied intelligence",
            "generalist robot",
            "robot foundation model",
        ],
        "context_keywords": [],
        "negative_keywords": [
            "spatial reasoning",
            "spatial grounding",
            "3d scene understanding",
        ],
    },
    "视觉-语言-动作模型": {
        "keywords": [
            "vision-language-action",
            "vision language action",
            "VLA",
            "multimodal robot",
        ],
        "context_keywords": [],
        "negative_keywords": ["vehicle-to-load"],
    },
    "机器人学习与强化学习": {
        "keywords": [
            "robot learning",
            "reinforcement learning",
            "imitation learning",
            "behavior cloning",
            "policy learning",
        ],
        "context_keywords": [
            "robot",
            "robotic",
            "humanoid",
            "manipulation",
            "locomotion",
            "real robot",
            "autonomous driving",
            "autonomous vehicle",
            "embodied",
        ],
        "negative_keywords": [],
    },
    "世界模型与规划": {
        "keywords": [
            "world model",
            "model-based planning",
            "predictive control",
            "latent dynamics",
        ],
        "context_keywords": [
            "robot",
            "robotic",
            "autonomous driving",
            "autonomous vehicle",
            "drone",
            "manipulation",
            "mobile robot",
        ],
        "negative_keywords": [],
    },
    "机器人操作与灵巧手": {
        "keywords": [
            "robot manipulation",
            "dexterous manipulation",
            "grasping",
            "bimanual",
            "tactile",
        ],
        "context_keywords": [
            "robot",
            "robotic",
            "manipulation",
            "grasp",
            "dexterous",
        ],
        "negative_keywords": [],
    },
    "导航与空间智能": {
        "keywords": [
            "robot navigation",
            "visual navigation",
            "spatial intelligence",
            "SLAM",
            "3D scene understanding",
        ],
        "context_keywords": [
            "robot",
            "robotic",
            "embodied",
            "autonomous driving",
            "autonomous vehicle",
            "mobile robot",
            "slam",
            "indoor navigation",
            "social navigation",
        ],
        "negative_keywords": [
            "camera motion",
            "video generation",
            "scene editing",
        ],
    },
}

_V3_DIRECTION_RULES = {
    str(item["name"]): {
        "keywords": list(item["keywords_json"]),
        "context_keywords": list(item["context_keywords_json"]),
        "negative_keywords": list(item["negative_keywords_json"]),
    }
    for item in DEFAULT_TOPICS
}
_V3_DIRECTION_RULES["导航与空间智能"] = {
    **_V3_DIRECTION_RULES["导航与空间智能"],
    "context_keywords": [
        "robot",
        "robots",
        "robotic",
        "robotics",
        "embodied",
        "autonomous driving",
        "autonomous vehicle",
        "autonomous vehicles",
        "mobile robot",
        "mobile robots",
        "slam",
        "indoor navigation",
        "social navigation",
    ],
    "negative_keywords": [
        "camera motion",
        "video generation",
        "scene editing",
        "question answering",
        "dense captioning",
    ],
}


def seed_defaults(session: Session) -> None:
    from .models import AppSetting, Profile, Topic

    profile = session.scalar(select(Profile).where(Profile.name == DEFAULT_PROFILE_NAME))
    if profile is None:
        profile = Profile(
            name=DEFAULT_PROFILE_NAME,
            description=(
                "关注具身智能、机器人基础模型、视觉-语言-动作模型、机器人学习、"
                "世界模型、操作与导航；优先可复现且有真实机器人证据的工作。"
            ),
            language="zh-CN",
            active=True,
        )
        session.add(profile)
        session.flush()
        for values in DEFAULT_TOPICS:
            session.add(Topic(profile_id=profile.id, enabled=True, **values))

    existing_keys = set(session.scalars(select(AppSetting.key)).all())
    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing_keys:
            session.add(AppSetting(key=key, value_json=value))
    # Migrate the prototype defaults without overwriting explicit user choices.
    legacy_defaults = {
        "schedule_time": "08:00",
        "max_results": 100,
        "deep_analysis_top_n": 10,
    }
    for key, old_value in legacy_defaults.items():
        setting = session.get(AppSetting, key)
        if setting is not None and setting.value == old_value:
            setting.value = DEFAULT_SETTINGS[key]
    cost_migration = session.get(AppSetting, _COST_PRESET_MIGRATION_KEY)
    if cost_migration is None:
        # Move exact aggressive prototype defaults to a conservative daily
        # preset.  Already tuned values are preserved, and the marker prevents
        # future user choices from being reinterpreted on every startup.
        conservative_defaults = {
            "screen_top_n": 80,
            "deep_analysis_top_n": 40,
            "deep_relevance_threshold": 80,
        }
        for key, old_value in conservative_defaults.items():
            setting = session.get(AppSetting, key)
            if setting is not None and setting.value == old_value:
                setting.value = DEFAULT_SETTINGS[key]
        session.add(
            AppSetting(
                key=_COST_PRESET_MIGRATION_KEY,
                value_json=_COST_PRESET_MIGRATION_VERSION,
            )
        )

    def migrate_direction_rules(
        source_rules: dict[str, dict[str, list[str]]],
    ) -> None:
        default_by_name = {str(item["name"]): item for item in DEFAULT_TOPICS}
        for topic in session.scalars(
            select(Topic).join(Profile).where(Profile.name == DEFAULT_PROFILE_NAME)
        ).all():
            legacy = source_rules.get(topic.name)
            current_default = default_by_name.get(topic.name)
            if legacy is None or current_default is None or not topic.enabled:
                continue
            # Match the complete prior built-in signature.  Changing any of
            # these fields is treated as an explicit user customization.
            if not (
                topic.arxiv_categories == list(current_default["arxiv_categories_json"])
                and topic.keywords == list(legacy.get("keywords", current_default["keywords_json"]))
                and topic.context_keywords
                in (
                    legacy["context_keywords"],
                    list(current_default["context_keywords_json"]),
                )
                and topic.negative_keywords
                in (
                    legacy["negative_keywords"],
                    list(current_default["negative_keywords_json"]),
                )
                and topic.weight == current_default["weight"]
            ):
                continue
            topic.keywords = list(current_default["keywords_json"])
            topic.context_keywords = list(current_default["context_keywords_json"])
            topic.negative_keywords = list(current_default["negative_keywords_json"])

    rule_migration = session.get(AppSetting, _DIRECTION_RULE_MIGRATION_KEY)
    if rule_migration is None:
        migrate_direction_rules(_LEGACY_DIRECTION_RULES)
        session.add(
            AppSetting(
                key=_DIRECTION_RULE_MIGRATION_KEY,
                value_json=_DIRECTION_RULE_MIGRATION_VERSION,
            )
        )
    elif rule_migration.value in _LEGACY_DIRECTION_RULE_MIGRATION_VERSIONS:
        # A prior build created the marker before recognizing that the UI
        # normalizes ``all:`` prefixes in query strings.  Re-run once using the
        # content fields that actually define attribution.
        source_rules = {
            "v2-conservative-robotics-context": _V2_DIRECTION_RULES,
            "v3-explicit-word-forms": _V3_DIRECTION_RULES,
        }.get(rule_migration.value, _LEGACY_DIRECTION_RULES)
        migrate_direction_rules(source_rules)
        rule_migration.value = _DIRECTION_RULE_MIGRATION_VERSION
    session.commit()
