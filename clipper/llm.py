"""Appels à Claude avec sortie JSON contrainte, et analyse robuste de la réponse."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import costs
from .config import require_env
from .log import log


class LLMError(RuntimeError):
    pass


def load_prompt(cfg, name: str, **values) -> str:
    """Charge prompts/<name>.md et remplace les {{variables}}."""
    path: Path = cfg.prompts_dir / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    missing = re.findall(r"\{\{(\w+)\}\}", text)
    if missing:
        raise LLMError(f"Variables non remplacées dans {path.name} : {', '.join(sorted(set(missing)))}")
    return text


def extract_json(text: str):
    """Extrait le premier objet/tableau JSON d'un texte (tolère ```json, du texte autour)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Réponse non JSON : {text[:200]!r}")


class Claude:
    def __init__(self, cfg, conn):
        import anthropic

        self.cfg = cfg
        self.conn = conn
        self.model = cfg["llm"]["model"]
        self.client = anthropic.Anthropic(api_key=require_env("ANTHROPIC_API_KEY"), max_retries=4)

    def json_call(self, *, system: str, user: str, schema: dict, effort: str,
                  video_id: int | None, kind: str, max_tokens: int = 32000) -> dict:
        """Envoie une requête et renvoie le JSON décodé. Le coût est enregistré en base."""
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if self.cfg["llm"].get("fallback"):
            # En cas de refus, l'API relance automatiquement la requête sur un modèle de repli.
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        # Streaming : évite les délais d'attente HTTP sur les longues transcriptions.
        with self.client.beta.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()

        usd = costs.anthropic_cost(self.cfg, msg.model if msg.model in self.cfg["pricing"]["anthropic"]
                                   else self.model, msg.usage)
        costs.record(self.conn, video_id, kind, usd,
                     f"{msg.model} in={msg.usage.input_tokens} out={msg.usage.output_tokens}")
        log.debug("Claude (%s) : %d tokens en entrée, %d en sortie, %.4f $",
                  kind, msg.usage.input_tokens, msg.usage.output_tokens, usd)

        if msg.stop_reason == "refusal":
            raise LLMError("Claude a refusé la requête.")
        if msg.stop_reason == "max_tokens":
            raise LLMError("Réponse tronquée (max_tokens atteint).")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return extract_json(text)
