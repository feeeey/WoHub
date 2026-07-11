"""视觉中继：图片 → 视觉模型 → 结构化盘面描述文本。
规则确定（规格 §4）：配置了 vision_model 就一律走中继；没配置就直传主模型。"""
import os
from pydantic_ai import Agent
from config import settings
from agent.llm import build_model

VISION_SYSTEM = """你是K线图表读图员。客观描述图中可见的事实：
品种与周期（若可见）、趋势结构（高低点序列）、关键支撑/压力位、
显著K线形态、可见指标状态（如 MACD/RSI/均线）、成交量特征、异常之处。
只描述可见事实与数值，不给交易建议，不猜测图外信息。用中文，条目式。"""

_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def load_image(kind: str, filename: str) -> tuple[bytes, str]:
    dirs = {"upload": settings.chat_uploads_dir, "screenshot": settings.screenshots_dir}
    if kind not in dirs:
        raise ValueError(f"unknown image kind: {kind}")
    path = os.path.join(dirs[kind], filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(filename)[1].lower()
    with open(path, "rb") as f:
        return f.read(), _MEDIA.get(ext, "image/png")


def describe_image(cfg, image_bytes: bytes, media_type: str, extra: str = "") -> str:
    from pydantic_ai import BinaryContent
    model = build_model(cfg.vision_channel, cfg.vision_model)
    agent = Agent(model, output_type=str, system_prompt=VISION_SYSTEM)
    prompt = [extra or "请读取并描述这张K线图。",
              BinaryContent(data=image_bytes, media_type=media_type)]
    return agent.run_sync(prompt).output
