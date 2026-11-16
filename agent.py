"""Generic task-execution agent: ONE class that writes any section of any company's report.

For each writing task it
  1. retrieves the most relevant pages of the company's real report from the knowledge base,
  2. goes to the web only to fill gaps (thin retrieval, or when a reviewer asked for a fix),
  3. writes the section strictly from that evidence, within its scope boundaries, aware of what other
     sections already cover (no repetition), and to the length the plan budgeted,
  4. says "Not disclosed in the sources reviewed" for anything the evidence does not contain.

On failure it returns a FAILED output with an honest placeholder. It never fabricates text.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config import get_llm, settings
from knowledge import KnowledgeBase
from schemas import (EvidenceItem, ReportBlueprint, SourceDocument, TaskDefinition, TaskOutput, TaskStatus)
from skills import get_style_guidance
from tools import ResearchTools
from utils import clean_llm_text, digest, word_count

NOT_DISCLOSED = "Not disclosed in the sources reviewed"


@dataclass
class RunContext:
    """Shared, thread-safe state for one report run."""
    blueprint: ReportBlueprint
    kb: KnowledgeBase
    tools: ResearchTools
    draft_dir: str
    texts: Dict[str, str] = field(default_factory=dict)
    digests: Dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        os.makedirs(self.draft_dir, exist_ok=True)
        self.chapter_tasks: Dict[str, List[TaskDefinition]] = {}
        for t in self.blueprint.tasks:
            self.chapter_tasks.setdefault(t.chapter_id, []).append(t)
        self.chapter_titles = [c.title for c in self.blueprint.chapters]
        docs = self.blueprint.reference_documents
        self.docs_note = "\n".join(
            f"- {d.title}: period {d.fiscal_year or 'unknown'}"
            f"{'' if d.matches_requested_period else ' (MAY NOT BE the requested period)'}"
            for d in docs) or "- none found (only web snippets are available)"

    def record(self, task_id: str, text: str) -> None:
        with self.lock:
            self.texts[task_id] = text
            self.digests[task_id] = digest(text, 420)

    def snapshot_texts(self) -> Dict[str, str]:
        with self.lock:
            return dict(self.texts)


class GenericTaskExecutionAgent:
    def __init__(self, ctx: RunContext, agent_id: Optional[str] = None):
        self.ctx = ctx
        self.agent_id = agent_id or f"agent_{uuid.uuid4().hex[:6]}"
        self.llm = get_llm()
        self.from_cache = False

    # ------------------------------------------------------------------ main
    def execute_task(self, task: TaskDefinition, feedback: Optional[str] = None) -> TaskOutput:
        self.from_cache = False
        md_path = os.path.join(self.ctx.draft_dir, f"{task.task_id}.md")
        meta_path = os.path.join(self.ctx.draft_dir, f"{task.task_id}.json")

        if not feedback and os.path.exists(md_path):                      # resume support
            with open(md_path, "r", encoding="utf-8") as f:
                cached = f.read().strip()
            if word_count(cached) > 30:
                sources: List[str] = []
                if os.path.exists(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as f:
                        sources = json.load(f).get("sources", [])
                self.ctx.record(task.task_id, cached)
                self.from_cache = True
                return TaskOutput(task_id=task.task_id, chapter_id=task.chapter_id, markdown_content=cached,
                                  sources=sources, word_count=word_count(cached))

        print(f"[{self.agent_id}] Writing '{' > '.join(task.path)}' (~{task.target_words} words)")
        try:
            evidence = self._retrieve(task)
            if feedback or len(evidence) < settings.MIN_KB_HITS_BEFORE_WEB:
                evidence += self._web_evidence(task, start=len(evidence) + 1)
            self.ctx.tools.save_evidence(evidence)

            text = self._write(task, evidence, feedback)
            if (settings.EXPAND_PASSES > 0 and word_count(text) < 0.6 * task.target_words
                    and sum(len(e.text) for e in evidence) > 6000):
                for _ in range(settings.EXPAND_PASSES):
                    text = self._expand(task, text, evidence)
            body, sources = self._finalize(task, text, evidence)
        except Exception as e:
            print(f"[{self.agent_id}] FAILED '{task.title}': {e}")
            return TaskOutput(task_id=task.task_id, chapter_id=task.chapter_id, status=TaskStatus.FAILED,
                              markdown_content=f"*(This section could not be generated: {str(e)[:160]})*",
                              error_message=str(e))

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(body)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"sources": sources}, f)
        self.ctx.record(task.task_id, body)
        print(f"[{self.agent_id}] Done '{task.title}': {word_count(body)} words (target {task.target_words}), "
              f"{len(evidence)} evidence items")
        return TaskOutput(task_id=task.task_id, chapter_id=task.chapter_id, markdown_content=body,
                          evidence_used=evidence, sources=sources, word_count=word_count(body))

    # ------------------------------------------------------------- research
    def _retrieve(self, task: TaskDefinition) -> List[EvidenceItem]:
        k = settings.RETRIEVAL_TOP_K * (2 if task.kind in ("financial_statement", "data_table") else 1)
        queries = [f"{task.title} {task.what_to_cover}"[:300]] + list(task.search_queries)
        best: Dict[int, Tuple[object, float]] = {}
        for q in queries:
            for ch, sc in self.ctx.kb.search(q, k=k):
                if ch.chunk_id not in best or sc > best[ch.chunk_id][1]:
                    best[ch.chunk_id] = (ch, sc)
        top = sorted(best.values(), key=lambda x: -x[1])[:k]
        top.sort(key=lambda x: (x[0].doc_id, x[0].page or 0))               # reading order
        out = []
        for i, (ch, sc) in enumerate(top, 1):
            out.append(EvidenceItem(evidence_id=f"ev_{uuid.uuid4().hex[:8]}", task_id=task.task_id,
                                    text=ch.text, source_title=ch.doc_title, source_url=ch.url,
                                    page=ch.page, origin="knowledge_base", score=round(sc, 3)))
        return out

    def _web_evidence(self, task: TaskDefinition, start: int) -> List[EvidenceItem]:
        items: List[EvidenceItem] = []
        seen: List[str] = []
        for q in task.search_queries[:2]:
            for r in self.ctx.tools.web_search(q, max_results=settings.MAX_SEARCH_RESULTS):
                if r["href"] in seen:
                    continue
                seen.append(r["href"])
                if r.get("body"):
                    items.append(EvidenceItem(evidence_id=f"ev_{uuid.uuid4().hex[:8]}", task_id=task.task_id,
                                              text=r["body"], source_title=r.get("title", ""),
                                              source_url=r["href"], origin="web"))
        for url in seen[:2]:
            page_text = self.ctx.tools.scrape_url(url, max_chars=4000)
            if word_count(page_text) > 80:
                items.append(EvidenceItem(evidence_id=f"ev_{uuid.uuid4().hex[:8]}", task_id=task.task_id,
                                          text=page_text, source_title="(web page)", source_url=url, origin="web"))
        return items

    # -------------------------------------------------------------- writing
    @staticmethod
    def _label(e: EvidenceItem) -> str:
        if e.origin == "knowledge_base":
            return f"{e.source_title}, p.{e.page}" if e.page else e.source_title
        return f"Web: {e.source_title or ''} {e.source_url}".strip()

    def _evidence_block(self, evidence: List[EvidenceItem]) -> str:
        blocks, used = [], 0
        for i, e in enumerate(evidence, 1):
            b = f"[E{i}] ({self._label(e)})\n{e.text}"
            if used + len(b) > settings.MAX_EVIDENCE_CHARS:
                break
            blocks.append(b)
            used += len(b)
        return "\n\n".join(blocks) or "(no evidence found)"

    def _scope_block(self, task: TaskDefinition) -> str:
        ctx = self.ctx
        sib = [t for t in ctx.chapter_tasks.get(task.chapter_id, []) if t.task_id != task.task_id]
        lines, used = [], 0
        for t in sib:
            ln = f"- {' > '.join(t.path[1:]) or t.title}: {t.what_to_cover[:110]}"
            if used + len(ln) > 4500:
                lines.append("- ...")
                break
            lines.append(ln)
            used += len(ln)
        others = ", ".join(c for c in ctx.chapter_titles if c != task.path[0])
        with ctx.lock:
            done = [(t.title, ctx.digests[t.task_id]) for t in sib if t.task_id in ctx.digests]
        covered, used = [], 0
        for title, dg in done:
            ln = f"- {title}: {dg}"
            if used + len(ln) > 3000:
                break
            covered.append(ln)
            used += len(ln)
        return (f"OTHER PARTS OF THIS CHAPTER (they own their own content - do not write it here):\n"
                f"{chr(10).join(lines) or '(none)'}\n\nOTHER CHAPTERS: {others or '(none)'}\n\n"
                f"ALREADY WRITTEN IN THIS CHAPTER (do not repeat; refer to it in a clause if needed):\n"
                f"{chr(10).join(covered) or '(nothing yet)'}")

    def _dependency_block(self, task: TaskDefinition) -> str:
        ctx = self.ctx
        with ctx.lock:
            have = [d for d in task.dependencies if d in ctx.texts]
            if not have:
                return "(none)"
            if len(have) <= 8:
                return "\n".join(f"--- {ctx.blueprint.get_task(d).title} ---\n{ctx.texts[d][:1500]}" for d in have)
            out, used = [], 0
            for d in have:
                ln = f"- {ctx.blueprint.get_task(d).title}: {ctx.digests[d]}"
                if used + len(ln) > 9000:
                    break
                out.append(ln)
                used += len(ln)
            return "\n".join(out)

    def _system_prompt(self, task: TaskDefinition) -> str:
        p = self.ctx.blueprint.company_profile
        return (
            f"You are the senior report writer for the annual report of {p.company_name}. You write ONE part of it in the "
            f"formal voice of a published annual report, as this company's jurisdiction normally words it.\n"
            f"TRUTHFULNESS RULES (non-negotiable):\n"
            f"1. Use ONLY facts in the EVIDENCE or the EARLIER-SECTION CONTEXT. Never invent numbers, names, dates, "
            f"percentages, quotes, targets or tables.\n"
            f"2. If a required item is not in the evidence write \"{NOT_DISCLOSED}\" for that item and move on. Never fill "
            f"gaps with plausible-looking content.\n"
            f"3. Cite evidence inline as [E1], [E2]. Every figure carries its currency, unit and period. If evidence "
            f"comes from a different fiscal year than {self.ctx.blueprint.input_spec.financial_year}, say which year it is; "
            f"never present another year's figure as the requested year's.\n"
            f"4. Tables: Markdown with a header row and '| --- |' separator; every row has the same number of columns.\n"
            f"5. Stay inside this part's scope. Do not write what belongs to other parts; do not repeat what earlier "
            f"parts already say.\n"
            f"6. Length: aim for about {task.target_words} words where the evidence supports it. Be thorough where there "
            f"is data; never pad with generic filler or boilerplate to reach a length.\n"
            f"7. Output ONLY the Markdown body. Do NOT start with the part's own title (it is added automatically). "
            f"Use '####' sub-headings if useful. No preamble, no commentary.\n"
            f"STYLE FOR THIS KIND OF PART ({task.kind}): {get_style_guidance(task.kind)}"
        )

    def _write(self, task: TaskDefinition, evidence: List[EvidenceItem], feedback: Optional[str]) -> str:
        spec = self.ctx.blueprint.input_spec
        user = f"""PART TO WRITE: {' > '.join(task.path)}
REPORT: {self.ctx.blueprint.company_profile.company_name} - {spec.financial_year} - {spec.reporting_scope}
WHAT IT MUST COVER: {task.what_to_cover}
ACCEPTANCE CRITERIA:
{chr(10).join('- ' + c for c in task.validation_criteria) or '- (none)'}
TARGET LENGTH: about {task.target_words} words

REFERENCE DOCUMENTS AVAILABLE:
{self.ctx.docs_note}

{self._scope_block(task)}

EARLIER SECTIONS THIS PART BUILDS ON:
{self._dependency_block(task)}

EVIDENCE:
{self._evidence_block(evidence)}

{('REVIEWER FEEDBACK - FIX THESE ISSUES:' + chr(10) + feedback) if feedback else ''}"""
        max_tokens = max(1500, min(settings.MAX_OUTPUT_TOKENS, int(task.target_words * 3.2) + 600))
        resp = self.llm.invoke([{"role": "system", "content": self._system_prompt(task)},
                                {"role": "user", "content": user}], max_tokens=max_tokens)
        text = clean_llm_text(resp.content)
        if word_count(text) < 12:
            raise ValueError("LLM returned an empty or too-short section")
        return text

    def _expand(self, task: TaskDefinition, draft: str, evidence: List[EvidenceItem]) -> str:
        user = f"""The draft below for "{' > '.join(task.path)}" is {word_count(draft)} words; the target is about
{task.target_words}. The evidence contains more usable material than the draft covers.
Rewrite and extend it: add every relevant fact, figure, table row and explanation from the EVIDENCE that the draft
omits (keeping within this part's scope). Do not add filler, do not repeat yourself, do not invent anything.
Keep [E#] citations. Output ONLY the full improved Markdown body.

DRAFT:
{draft}

EVIDENCE:
{self._evidence_block(evidence)}"""
        try:
            resp = self.llm.invoke([{"role": "system", "content": self._system_prompt(task)},
                                    {"role": "user", "content": user}],
                                   max_tokens=max(1500, min(settings.MAX_OUTPUT_TOKENS, int(task.target_words * 3.2) + 600)))
            better = clean_llm_text(resp.content)
            return better if word_count(better) > word_count(draft) else draft
        except Exception:
            return draft

    # ------------------------------------------------------------ finishing
    def _finalize(self, task: TaskDefinition, text: str, evidence: List[EvidenceItem]) -> Tuple[str, List[str]]:
        lines = text.strip().splitlines()
        while lines and re.match(r"^#{1,3}\s", lines[0].strip()) and \
                re.sub(r"^#+\s*\d*[\.\)]?\s*", "", lines[0]).strip().lower() == task.title.strip().lower():
            lines.pop(0)                                                   # writer repeated the title
        text = "\n".join(lines).strip()

        cited = set()
        for m in re.finditer(r"\[((?:E\d+[\s,;]*)+)\]", text):
            cited.update(int(n) for n in re.findall(r"E(\d+)", m.group(1)))
        text = re.sub(r"\s*\[(?:E\d+[\s,;]*)+\]", "", text)

        groups: Dict[str, List[int]] = {}
        for n in sorted(cited):
            if 1 <= n <= len(evidence):
                e = evidence[n - 1]
                key = e.source_title if e.origin == "knowledge_base" else (e.source_url or e.source_title)
                groups.setdefault(key, [])
                if e.page and e.page not in groups[key]:
                    groups[key].append(e.page)
        sources = [f"{k} (p. {', '.join(map(str, v))})" if v else k for k, v in groups.items()]
        if sources:
            text += "\n\n*Sources: " + "; ".join(sources) + "*"
        return text, sources