"""Versioned cross-domain AI method vocabulary and local candidate discovery."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .state import clean_string_list

METHOD_CATALOG_VERSION = "2026.08.1"
METHOD_CANDIDATE_WINDOW_DAYS = 30
METHOD_CANDIDATE_MIN_PAPERS = 2


@dataclass(frozen=True)
class MethodTerm:
    slug: str
    group: str
    name: str
    aliases: tuple[str, ...]
    context_terms: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    description: str = ""
    source: str = "built_in"

    def wire(self) -> dict[str, Any]:
        return asdict(self)


def _term(
    slug: str,
    group: str,
    name: str,
    *aliases: str,
    context: Sequence[str] = (),
    exclusions: Sequence[str] = (),
    description: str = "",
) -> MethodTerm:
    return MethodTerm(
        slug=slug,
        group=group,
        name=name,
        aliases=tuple(dict.fromkeys((name, *aliases))),
        context_terms=tuple(context),
        exclusions=tuple(exclusions),
        description=description,
    )


# Stable method families, not tasks, datasets, products, paper sections, or free-form claims.
METHOD_CATALOG: tuple[MethodTerm, ...] = (
    # Learning paradigms and optimization
    _term("supervised-learning", "学习范式与优化", "监督学习", "supervised learning"),
    _term(
        "semi-supervised-learning",
        "学习范式与优化",
        "半监督学习",
        "semi-supervised learning",
        "semisupervised learning",
    ),
    _term(
        "self-supervised-learning",
        "学习范式与优化",
        "自监督学习",
        "self-supervised learning",
        "self supervision",
        "self-supervision",
    ),
    _term(
        "contrastive-learning",
        "学习范式与优化",
        "对比学习",
        "contrastive learning",
        "contrastive objective",
        "contrastive loss",
    ),
    _term("metric-learning", "学习范式与优化", "度量学习", "metric learning", "triplet loss"),
    _term(
        "multi-task-learning",
        "学习范式与优化",
        "多任务学习",
        "multi-task learning",
        "multitask learning",
    ),
    _term(
        "meta-learning",
        "学习范式与优化",
        "元学习",
        "meta-learning",
        "meta learning",
        "learning to learn",
    ),
    _term(
        "continual-learning",
        "学习范式与优化",
        "持续学习",
        "continual learning",
        "lifelong learning",
    ),
    _term("active-learning", "学习范式与优化", "主动学习", "active learning"),
    _term(
        "curriculum-learning",
        "学习范式与优化",
        "课程学习",
        "curriculum learning",
        "self-paced learning",
    ),
    _term("transfer-learning", "学习范式与优化", "迁移学习", "transfer learning"),
    _term(
        "domain-adaptation",
        "学习范式与优化",
        "领域适应",
        "domain adaptation",
        "test-time adaptation",
        "source-free adaptation",
    ),
    _term(
        "multi-objective-optimization",
        "学习范式与优化",
        "多目标优化",
        "multi-objective optimization",
        "multiobjective optimization",
        "pareto optimization",
    ),
    _term(
        "gradient-free-optimization",
        "学习范式与优化",
        "无梯度优化",
        "gradient-free optimization",
        "derivative-free optimization",
        "evolutionary strategy",
        "evolution strategies",
    ),
    # Foundation models, adaptation, and alignment
    _term(
        "pretraining",
        "基础模型训练与适配",
        "预训练",
        "pretraining",
        "pre-training",
        "pretrained from scratch",
    ),
    _term(
        "instruction-tuning",
        "基础模型训练与适配",
        "指令微调",
        "instruction tuning",
        "instruction fine-tuning",
        "instruction finetuning",
    ),
    _term(
        "supervised-fine-tuning",
        "基础模型训练与适配",
        "监督微调",
        "supervised fine-tuning",
        "supervised finetuning",
        "SFT",
    ),
    _term(
        "parameter-efficient-tuning",
        "基础模型训练与适配",
        "参数高效微调",
        "parameter-efficient fine-tuning",
        "parameter efficient fine tuning",
        "PEFT",
        "LoRA",
        "QLoRA",
        "low-rank adaptation",
    ),
    _term(
        "prompt-tuning",
        "基础模型训练与适配",
        "提示微调",
        "prompt tuning",
        "prefix tuning",
        "soft prompt",
    ),
    _term(
        "in-context-learning",
        "基础模型训练与适配",
        "上下文学习",
        "in-context learning",
        "in context learning",
        "few-shot prompting",
    ),
    _term(
        "knowledge-distillation",
        "基础模型训练与适配",
        "知识蒸馏",
        "knowledge distillation",
        "teacher-student distillation",
        "teacher student distillation",
    ),
    _term(
        "rlhf",
        "基础模型训练与适配",
        "人类反馈强化学习",
        "RLHF",
        "reinforcement learning from human feedback",
    ),
    _term(
        "rlai-feedback",
        "基础模型训练与适配",
        "AI 反馈强化学习",
        "RLAIF",
        "reinforcement learning from ai feedback",
    ),
    _term(
        "preference-optimization",
        "基础模型训练与适配",
        "偏好优化",
        "preference optimization",
        "direct preference optimization",
        "DPO",
        "IPO",
        "ORPO",
        "KTO",
    ),
    _term(
        "constitutional-ai",
        "基础模型训练与适配",
        "宪法式 AI",
        "constitutional AI",
        "constitutional alignment",
    ),
    _term(
        "mixture-of-experts",
        "基础模型训练与适配",
        "混合专家",
        "mixture of experts",
        "mixture-of-experts",
        "MoE",
        "sparse experts",
    ),
    _term(
        "retrieval-augmented-generation",
        "基础模型训练与适配",
        "检索增强生成",
        "retrieval-augmented generation",
        "retrieval augmented generation",
        "RAG",
        exclusions=("ragged",),
    ),
    _term(
        "tool-augmented-learning",
        "基础模型训练与适配",
        "工具增强学习",
        "tool-augmented learning",
        "tool augmented language model",
        "tool learning",
        "function calling",
    ),
    _term(
        "long-context-modeling",
        "基础模型训练与适配",
        "长上下文建模",
        "long-context modeling",
        "long context modeling",
        "context window extension",
        "context length extrapolation",
    ),
    # Generative modeling
    _term(
        "autoregressive-modeling",
        "生成模型",
        "自回归建模",
        "autoregressive modeling",
        "autoregressive generation",
        "auto-regressive modeling",
    ),
    _term(
        "diffusion-models",
        "生成模型",
        "扩散模型",
        "diffusion model",
        "diffusion models",
        "denoising diffusion",
        "score-based generative",
    ),
    _term(
        "flow-matching",
        "生成模型",
        "流匹配",
        "flow matching",
        "rectified flow",
        "conditional flow matching",
    ),
    _term(
        "generative-adversarial-networks",
        "生成模型",
        "生成对抗网络",
        "generative adversarial network",
        "generative adversarial networks",
        "GAN",
        "GANs",
    ),
    _term(
        "variational-autoencoders",
        "生成模型",
        "变分自编码器",
        "variational autoencoder",
        "variational autoencoders",
        "VAE",
        "VAEs",
    ),
    _term("normalizing-flows", "生成模型", "归一化流", "normalizing flow", "normalizing flows"),
    _term(
        "energy-based-models",
        "生成模型",
        "能量模型",
        "energy-based model",
        "energy based model",
        "energy-based models",
    ),
    _term(
        "masked-modeling",
        "生成模型",
        "掩码建模",
        "masked language modeling",
        "masked image modeling",
        "masked modeling",
        "mask prediction",
    ),
    # Reasoning, search, planning, and agents
    _term(
        "chain-of-thought",
        "推理、规划与智能体",
        "思维链推理",
        "chain-of-thought",
        "chain of thought",
        "CoT prompting",
    ),
    _term(
        "test-time-compute",
        "推理、规划与智能体",
        "测试时计算",
        "test-time compute",
        "test time compute",
        "inference-time scaling",
        "inference time scaling",
    ),
    _term(
        "self-consistency",
        "推理、规划与智能体",
        "自一致性推理",
        "self-consistency decoding",
        "self consistency decoding",
        "self-consistency prompting",
    ),
    _term(
        "search-based-reasoning",
        "推理、规划与智能体",
        "搜索增强推理",
        "search-based reasoning",
        "search augmented reasoning",
        "tree of thoughts",
        "tree-of-thought",
    ),
    _term(
        "verification-guided-reasoning",
        "推理、规划与智能体",
        "验证器引导推理",
        "verifier-guided reasoning",
        "verification-guided reasoning",
        "process reward model",
        "outcome reward model",
    ),
    _term(
        "model-based-planning",
        "推理、规划与智能体",
        "基于模型的规划",
        "model-based planning",
        "latent planning",
        "planning with learned models",
    ),
    _term(
        "world-models",
        "推理、规划与智能体",
        "世界模型",
        "world model",
        "world models",
        "latent dynamics model",
    ),
    _term(
        "agent-memory",
        "推理、规划与智能体",
        "智能体记忆",
        "agent memory",
        "long-term agent memory",
        "episodic memory",
        context=("agent", "llm", "language model"),
    ),
    _term(
        "reflection",
        "推理、规划与智能体",
        "反思与自我修正",
        "self-reflection",
        "self reflection",
        "reflexion",
        "self-correction",
        "self correction",
        context=("agent", "reasoning", "language model", "llm"),
    ),
    _term(
        "multi-agent-coordination",
        "推理、规划与智能体",
        "多智能体协作",
        "multi-agent coordination",
        "multi-agent collaboration",
        "multi agent debate",
        "agent debate",
    ),
    # Vision and multimodal learning
    _term(
        "transformers",
        "视觉与多模态",
        "Transformer",
        "transformer architecture",
        "transformer-based",
        "transformer network",
    ),
    _term(
        "vision-transformers",
        "视觉与多模态",
        "视觉 Transformer",
        "vision transformer",
        "vision transformers",
        "ViT",
    ),
    _term(
        "convolutional-networks",
        "视觉与多模态",
        "卷积神经网络",
        "convolutional neural network",
        "convolutional neural networks",
        "CNN",
        "CNNs",
    ),
    _term(
        "cross-attention",
        "视觉与多模态",
        "交叉注意力",
        "cross-attention",
        "cross attention",
        "co-attention",
    ),
    _term(
        "vision-language-pretraining",
        "视觉与多模态",
        "视觉语言预训练",
        "vision-language pretraining",
        "vision language pretraining",
        "contrastive language-image pretraining",
    ),
    _term(
        "multimodal-fusion",
        "视觉与多模态",
        "多模态融合",
        "multimodal fusion",
        "cross-modal fusion",
        "modality fusion",
    ),
    _term(
        "nerf",
        "视觉与多模态",
        "神经辐射场",
        "neural radiance field",
        "neural radiance fields",
        "NeRF",
        "NeRFs",
    ),
    _term(
        "gaussian-splatting",
        "视觉与多模态",
        "高斯泼溅",
        "gaussian splatting",
        "3D Gaussian Splatting",
        "3DGS",
    ),
    # Speech and audio
    _term(
        "ctc",
        "语音与音频",
        "连接时序分类",
        "connectionist temporal classification",
        "CTC loss",
        "CTC-based",
    ),
    _term(
        "transducer",
        "语音与音频",
        "语音转导器",
        "RNN-T",
        "RNNT",
        "neural transducer",
        "transducer model",
        context=("speech", "audio", "asr", "recognition"),
    ),
    _term(
        "self-supervised-speech",
        "语音与音频",
        "自监督语音表征",
        "self-supervised speech",
        "self-supervised audio",
        "masked acoustic modeling",
    ),
    _term(
        "neural-audio-codecs",
        "语音与音频",
        "神经音频编解码",
        "neural audio codec",
        "neural audio codecs",
        "audio tokenizer",
        "codec language model",
    ),
    _term(
        "neural-vocoders",
        "语音与音频",
        "神经声码器",
        "neural vocoder",
        "neural vocoders",
        "neural waveform generation",
    ),
    # Graph learning
    _term(
        "graph-neural-networks",
        "图学习",
        "图神经网络",
        "graph neural network",
        "graph neural networks",
        "GNN",
        "GNNs",
    ),
    _term(
        "graph-convolution",
        "图学习",
        "图卷积",
        "graph convolutional network",
        "graph convolution",
        "GCN",
        "GCNs",
    ),
    _term(
        "message-passing",
        "图学习",
        "消息传递神经网络",
        "message passing neural network",
        "message-passing neural network",
        "MPNN",
        context=("graph", "molecular", "node", "edge"),
    ),
    _term(
        "graph-transformers", "图学习", "图 Transformer", "graph transformer", "graph transformers"
    ),
    _term(
        "knowledge-graph-embedding",
        "图学习",
        "知识图谱嵌入",
        "knowledge graph embedding",
        "knowledge graph embeddings",
        "link prediction embedding",
    ),
    # Reinforcement learning and control
    _term(
        "reinforcement-learning",
        "强化学习与控制",
        "强化学习",
        "reinforcement learning",
        "policy gradient",
        "actor-critic",
        "actor critic",
    ),
    _term(
        "imitation-learning",
        "强化学习与控制",
        "模仿学习",
        "imitation learning",
        "learning from demonstrations",
        "behavior cloning",
        "behaviour cloning",
    ),
    _term(
        "offline-rl",
        "强化学习与控制",
        "离线强化学习",
        "offline reinforcement learning",
        "offline RL",
        "batch reinforcement learning",
    ),
    _term(
        "inverse-rl", "强化学习与控制", "逆强化学习", "inverse reinforcement learning", "inverse RL"
    ),
    _term(
        "model-based-rl",
        "强化学习与控制",
        "基于模型的强化学习",
        "model-based reinforcement learning",
        "model based reinforcement learning",
        "model-based RL",
    ),
    _term(
        "multi-agent-rl",
        "强化学习与控制",
        "多智能体强化学习",
        "multi-agent reinforcement learning",
        "multi agent reinforcement learning",
        "MARL",
    ),
    _term(
        "hierarchical-rl",
        "强化学习与控制",
        "分层强化学习",
        "hierarchical reinforcement learning",
        "hierarchical RL",
        "options framework",
    ),
    _term(
        "contextual-bandits",
        "强化学习与控制",
        "上下文赌博机",
        "contextual bandit",
        "contextual bandits",
        "multi-armed bandit",
    ),
    _term(
        "model-predictive-control",
        "强化学习与控制",
        "模型预测控制",
        "model predictive control",
        "model-predictive control",
        "MPC",
        context=("control", "robot", "system", "dynamics", "trajectory"),
    ),
    # Robotics and embodied intelligence
    _term(
        "vision-language-action",
        "机器人与具身智能",
        "视觉-语言-动作模型",
        "vision-language-action",
        "vision language action",
        "VLA",
        "VLA model",
        "VLA models",
        context=("robot", "action", "vision", "language", "embodied", "cs.RO"),
        exclusions=("vehicle-to-load",),
    ),
    _term(
        "diffusion-policy",
        "机器人与具身智能",
        "扩散策略",
        "diffusion policy",
        "diffusion policies",
        context=("robot", "manipulation", "control", "action"),
    ),
    _term(
        "sim-to-real",
        "机器人与具身智能",
        "仿真到现实迁移",
        "sim-to-real",
        "sim2real",
        "simulation-to-reality",
        "simulation to reality",
    ),
    _term(
        "domain-randomization",
        "机器人与具身智能",
        "域随机化",
        "domain randomization",
        "dynamics randomization",
        context=("robot", "simulation", "simulator", "control"),
    ),
    _term(
        "slam",
        "机器人与具身智能",
        "同步定位与建图",
        "simultaneous localization and mapping",
        "visual SLAM",
        "LiDAR SLAM",
        "SLAM",
        context=("robot", "navigation", "mapping", "localization", "lidar", "camera"),
    ),
    _term(
        "motion-planning",
        "机器人与具身智能",
        "运动规划",
        "motion planning",
        "trajectory planning",
        "sampling-based planning",
        context=("robot", "manipulator", "autonomous", "collision", "control"),
    ),
    _term(
        "visual-servoing",
        "机器人与具身智能",
        "视觉伺服",
        "visual servoing",
        "vision-based control",
        context=("robot", "manipulation", "control"),
    ),
    _term(
        "sensor-fusion",
        "机器人与具身智能",
        "多传感器融合",
        "sensor fusion",
        "multi-sensor fusion",
        "multisensor fusion",
        context=("robot", "autonomous", "lidar", "imu", "camera", "navigation"),
    ),
    # Trustworthy AI, safety, and privacy
    _term(
        "adversarial-training",
        "可信、安全与隐私",
        "对抗训练",
        "adversarial training",
        "adversarial robustness training",
    ),
    _term(
        "differential-privacy",
        "可信、安全与隐私",
        "差分隐私",
        "differential privacy",
        "differentially private",
        "DP-SGD",
    ),
    _term(
        "secure-aggregation",
        "可信、安全与隐私",
        "安全聚合",
        "secure aggregation",
        "privacy-preserving aggregation",
    ),
    _term(
        "red-teaming",
        "可信、安全与隐私",
        "红队评测",
        "red teaming",
        "red-teaming",
        "adversarial evaluation",
    ),
    _term(
        "mechanistic-interpretability",
        "可信、安全与隐私",
        "机制可解释性",
        "mechanistic interpretability",
        "circuit analysis",
        "activation patching",
    ),
    _term(
        "explainable-ai",
        "可信、安全与隐私",
        "可解释人工智能",
        "explainable AI",
        "XAI",
        "post-hoc explanation",
        "feature attribution",
    ),
    _term(
        "uncertainty-calibration",
        "可信、安全与隐私",
        "不确定性校准",
        "uncertainty calibration",
        "confidence calibration",
        "temperature scaling",
        "calibration error",
    ),
    _term(
        "conformal-prediction",
        "可信、安全与隐私",
        "保形预测",
        "conformal prediction",
        "conformal inference",
        "conformal risk control",
    ),
    _term(
        "machine-unlearning",
        "可信、安全与隐私",
        "机器遗忘",
        "machine unlearning",
        "model unlearning",
    ),
    _term(
        "watermarking",
        "可信、安全与隐私",
        "生成内容水印",
        "model watermarking",
        "text watermarking",
        "generative watermarking",
        "watermark detection",
    ),
    # Efficient training and inference
    _term(
        "quantization",
        "高效训练与推理系统",
        "量化",
        "model quantization",
        "weight quantization",
        "activation quantization",
        "post-training quantization",
        "quantization-aware training",
        "int8 quantization",
        "int4 quantization",
    ),
    _term(
        "pruning",
        "高效训练与推理系统",
        "模型剪枝",
        "model pruning",
        "structured pruning",
        "unstructured pruning",
        "network pruning",
    ),
    _term(
        "speculative-decoding",
        "高效训练与推理系统",
        "投机解码",
        "speculative decoding",
        "speculative sampling",
        "draft model decoding",
    ),
    _term(
        "kv-cache",
        "高效训练与推理系统",
        "KV 缓存优化",
        "KV cache",
        "key-value cache",
        "paged attention",
        "KV cache compression",
    ),
    _term(
        "flash-attention",
        "高效训练与推理系统",
        "FlashAttention",
        "flash attention",
        "FlashAttention",
    ),
    _term(
        "sparse-attention",
        "高效训练与推理系统",
        "稀疏注意力",
        "sparse attention",
        "block-sparse attention",
        "local attention",
    ),
    _term(
        "distributed-training",
        "高效训练与推理系统",
        "分布式训练",
        "distributed training",
        "data parallelism",
        "tensor parallelism",
        "pipeline parallelism",
        "fully sharded data parallel",
    ),
    _term(
        "low-precision-training",
        "高效训练与推理系统",
        "低精度训练",
        "mixed precision training",
        "low-precision training",
        "FP8 training",
        "BF16 training",
    ),
    # Causality, probabilistic learning, and uncertainty
    _term(
        "causal-inference",
        "因果、概率与不确定性",
        "因果推断",
        "causal inference",
        "causal discovery",
        "structural causal model",
    ),
    _term(
        "causal-representation-learning",
        "因果、概率与不确定性",
        "因果表征学习",
        "causal representation learning",
        "disentangled causal representation",
    ),
    _term(
        "bayesian-deep-learning",
        "因果、概率与不确定性",
        "贝叶斯深度学习",
        "bayesian deep learning",
        "bayesian neural network",
        "Bayesian neural networks",
    ),
    _term(
        "variational-inference",
        "因果、概率与不确定性",
        "变分推断",
        "variational inference",
        "amortized inference",
        "evidence lower bound",
        "ELBO",
    ),
    _term(
        "monte-carlo",
        "因果、概率与不确定性",
        "蒙特卡洛推断",
        "Markov chain Monte Carlo",
        "MCMC",
        "Monte Carlo inference",
        context=("bayesian", "posterior", "sampling", "inference"),
    ),
    _term(
        "gaussian-processes",
        "因果、概率与不确定性",
        "高斯过程",
        "gaussian process",
        "gaussian processes",
        "deep gaussian process",
    ),
    _term(
        "ensemble-learning",
        "因果、概率与不确定性",
        "集成学习",
        "ensemble learning",
        "deep ensemble",
        "model ensemble",
        "bagging",
        "boosting",
    ),
    # AI for Science and geometric learning
    _term(
        "geometric-deep-learning",
        "AI for Science 与几何学习",
        "几何深度学习",
        "geometric deep learning",
        "geometry-aware learning",
    ),
    _term(
        "equivariant-networks",
        "AI for Science 与几何学习",
        "等变神经网络",
        "equivariant neural network",
        "equivariant neural networks",
        "E(3)-equivariant",
        "SE(3)-equivariant",
    ),
    _term(
        "neural-operators",
        "AI for Science 与几何学习",
        "神经算子",
        "neural operator",
        "neural operators",
        "Fourier neural operator",
        "operator learning",
    ),
    _term(
        "physics-informed-networks",
        "AI for Science 与几何学习",
        "物理信息神经网络",
        "physics-informed neural network",
        "physics informed neural network",
        "PINN",
        "PINNs",
    ),
    _term(
        "differentiable-simulation",
        "AI for Science 与几何学习",
        "可微分仿真",
        "differentiable simulation",
        "differentiable simulator",
        "differentiable physics",
    ),
    _term(
        "graphical-flow-networks",
        "AI for Science 与几何学习",
        "生成流网络",
        "generative flow network",
        "generative flow networks",
        "GFlowNet",
        "GFlowNets",
    ),
    _term(
        "inverse-design",
        "AI for Science 与几何学习",
        "逆向设计",
        "inverse design",
        "generative design",
        context=("material", "molecule", "protein", "physics", "engineering", "structure"),
    ),
)


def normalize_method_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s_\-‐‑‒–—]+", "", text)


def clean_custom_methods(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = normalize_method_key(name)
        if not name or not key or key in seen:
            continue
        result.append(
            {
                "name": name,
                "aliases": clean_string_list(item.get("aliases", []), limit=20),
            }
        )
        seen.add(key)
    return result


def _has_term(text: str, term: str) -> bool:
    phrase = term.strip()
    if not phrase:
        return False
    if re.fullmatch(r"[A-Za-z0-9_+.'-]+", phrase):
        return (
            re.search(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", text, re.I)
            is not None
        )
    return (
        unicodedata.normalize("NFKC", phrase).casefold()
        in unicodedata.normalize("NFKC", text).casefold()
    )


def custom_method_terms(values: Iterable[Mapping[str, Any]] | None) -> tuple[MethodTerm, ...]:
    result: list[MethodTerm] = []
    occupied = {normalize_method_key(alias) for item in METHOD_CATALOG for alias in item.aliases}
    for index, value in enumerate(values or []):
        name = str(value.get("name") or "").strip()
        if not name or normalize_method_key(name) in occupied:
            continue
        aliases = [str(item).strip() for item in value.get("aliases", []) if str(item).strip()]
        unique_aliases = tuple(dict.fromkeys((name, *aliases)))
        keys = {normalize_method_key(alias) for alias in unique_aliases}
        if not keys or keys & occupied:
            continue
        occupied.update(keys)
        result.append(
            MethodTerm(
                slug=f"custom-{index + 1}-{normalize_method_key(name)[:32]}",
                group="自定义方法",
                name=name,
                aliases=unique_aliases,
                description="由用户审核接受的本地方法标签。",
                source="custom",
            )
        )
    return tuple(result)


def catalog_with_custom(
    values: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[MethodTerm, ...]:
    return (*METHOD_CATALOG, *custom_method_terms(values))


def match_method_labels(
    text: str,
    custom_values: Iterable[Mapping[str, Any]] | None = None,
) -> list[str]:
    labels: list[str] = []
    for item in catalog_with_custom(custom_values):
        if item.exclusions and any(_has_term(text, term) for term in item.exclusions):
            continue
        if not any(_has_term(text, alias) for alias in item.aliases):
            continue
        if item.context_terms and not any(_has_term(text, term) for term in item.context_terms):
            continue
        labels.append(item.name)
    return labels


def _candidate_phrase(value: object) -> str | None:
    phrase = unicodedata.normalize("NFKC", str(value or "")).strip(" \t\r\n,，;；")
    phrase = re.sub(r"\s+", " ", phrase)
    if not 2 <= len(phrase) <= 80:
        return None
    if any(mark in phrase for mark in ("。", "！", "？", "；", "\n", ": ", "：")):
        return None
    if re.search(r"[.!?;](?:\s|$)", phrase):
        return None
    if len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+.'/-]*", phrase)) > 12:
        return None
    if re.match(r"^(we |our |this |the paper |本文|我们|该文|提出|一种)", phrase, re.I):
        return None
    return phrase


def discover_method_candidates(
    records: Iterable[Mapping[str, Any]],
    *,
    custom_values: Iterable[Mapping[str, Any]] | None = None,
    dismissed: Iterable[str] = (),
    min_papers: int = METHOD_CANDIDATE_MIN_PAPERS,
) -> list[dict[str, Any]]:
    known = {
        normalize_method_key(alias)
        for item in catalog_with_custom(custom_values)
        for alias in item.aliases
    }
    dismissed_keys = {normalize_method_key(item) for item in dismissed}
    papers: defaultdict[str, set[int]] = defaultdict(set)
    display: dict[str, str] = {}
    examples: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        paper_id = int(record.get("paper_id") or 0)
        if not paper_id:
            continue
        for raw in record.get("methods", []) or []:
            phrase = _candidate_phrase(raw)
            if not phrase:
                continue
            key = normalize_method_key(phrase)
            if (
                not key
                or key in known
                or key in dismissed_keys
                or match_method_labels(phrase, custom_values)
            ):
                continue
            display.setdefault(key, phrase)
            papers[key].add(paper_id)
            if not any(item["paper_id"] == paper_id for item in examples[key]):
                examples[key].append(
                    {
                        "paper_id": paper_id,
                        "arxiv_id": str(record.get("arxiv_id") or ""),
                        "title": str(record.get("title") or ""),
                    }
                )
    keys = sorted(
        (key for key, paper_ids in papers.items() if len(paper_ids) >= max(2, min_papers)),
        key=lambda key: (-len(papers[key]), display[key].casefold()),
    )
    return [
        {
            "key": key,
            "name": display[key],
            "paper_count": len(papers[key]),
            "paper_ids": sorted(papers[key]),
            "representative_papers": examples[key][:3],
            "status": "pending",
        }
        for key in keys
    ]


def method_catalog_payload(
    custom_values: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    items = catalog_with_custom(custom_values)
    groups = list(dict.fromkeys(item.group for item in items))
    return {
        "version": METHOD_CATALOG_VERSION,
        "count": len(items),
        "built_in_count": len(METHOD_CATALOG),
        "custom_count": len(items) - len(METHOD_CATALOG),
        "groups": groups,
        "items": [item.wire() for item in items],
        "scope_note": (
            "广泛覆盖主要稳定 AI 方法族，不包含任务、数据集、产品名或论文贡献长句；"
            "新候选必须跨论文重复并经用户审核后才能进入日报。"
        ),
    }


__all__ = [
    "METHOD_CANDIDATE_MIN_PAPERS",
    "METHOD_CANDIDATE_WINDOW_DAYS",
    "METHOD_CATALOG",
    "METHOD_CATALOG_VERSION",
    "MethodTerm",
    "catalog_with_custom",
    "custom_method_terms",
    "discover_method_candidates",
    "match_method_labels",
    "method_catalog_payload",
    "normalize_method_key",
]
