"""Built-in followed-topic seed data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def seed_defaults(session: Session) -> None:
    from .models import Profile, Topic

    profile = session.scalar(select(Profile).order_by(Profile.id))
    if profile is None:
        profile = Profile()
        session.add(profile)
        session.flush()
        for values in DEFAULT_TOPICS:
            session.add(Topic(profile_id=profile.id, enabled=True, **values))
    session.commit()
