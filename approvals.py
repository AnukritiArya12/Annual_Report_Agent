"""Human approval checkpoints (human-in-the-loop).

Purpose: make sure the agent only ever uses documents of the company you asked for - not another
company, not a look-alike, not a subsidiary's own report.

Rules of this module
  * Every question is asked in the MAIN thread, before the parallel writing starts (never inside workers).
  * Every answer is saved in  runs/<company>_<fy>/approvals.json  so --resume does not ask again.
  * --auto-approve answers every question with the agent's own proposal (= the old, unattended behaviour).
  * Typing 'abort' at any prompt stops the run cleanly. Closing the terminal / no keyboard also aborts
    (it never silently approves).
  * Approved sources are remembered as a set of DOMAINS. Later, the per-section web fallback in agent.py may
    only use pages from those domains that also mention the company (see filter_web_results).
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse


class ApprovalAborted(Exception):
    """The human typed 'abort' (or no keyboard is available to ask)."""


_MISSING = object()
_state: Dict[str, Any] = {"path": "", "auto": False, "decisions": {}}
_lock = threading.RLock()        # protects _state
_io_lock = threading.Lock()      # one question on the screen at a time
_APPROVED_KEY = "_approved_domains"
_CORP = {"inc", "corp", "corporation", "ltd", "limited", "plc", "llc", "company", "co", "group", "holdings",
         "holding", "the", "and", "sa", "ag", "nv", "se", "pvt", "private", "public", "llp"}


# --------------------------------------------------------------------------- setup / persistence
def configure(run_dir: str, auto: bool = False, reset: bool = False) -> None:
    """Call once per run. reset=True forgets old answers (a fresh run); reset=False keeps them (--resume)."""
    with _lock:
        _state["path"] = os.path.join(run_dir, "approvals.json")
        _state["auto"] = bool(auto)
        _state["decisions"] = {}
        if not reset and os.path.exists(_state["path"]):
            try:
                with open(_state["path"], "r", encoding="utf-8") as f:
                    _state["decisions"] = json.load(f)
            except Exception:
                _state["decisions"] = {}
        _save()


def _save() -> None:
    if not _state["path"]:
        return
    try:
        os.makedirs(os.path.dirname(_state["path"]), exist_ok=True)
        with open(_state["path"], "w", encoding="utf-8") as f:
            json.dump(_state["decisions"], f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _recall(key: str) -> Any:
    with _lock:
        return _state["decisions"].get(key, _MISSING)


def _remember(key: str, value: Any) -> None:
    with _lock:
        _state["decisions"][key] = value
        _save()


def is_auto() -> bool:
    return bool(_state["auto"])


# --------------------------------------------------------------------------- terminal helpers
def _banner(title: str, lines: Sequence[str]) -> None:
    print("\n" + "-" * 72)
    print(f"  HUMAN APPROVAL: {title}")
    print("-" * 72)
    for ln in lines:
        print(f"  {ln}")


def _ask(prompt: str) -> str:
    try:
        ans = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise ApprovalAborted("no answer received (run in a normal terminal, or use --auto-approve)")
    if ans.lower() in ("abort", "quit", "exit"):
        raise ApprovalAborted("stopped by user")
    return ans


# --------------------------------------------------------------------------- yes / no
def confirm(key: str, title: str, lines: Sequence[str], default: bool = True, remember_no: bool = True) -> bool:
    """Yes/no question. 'default' is what plain Enter means. remember_no=False: a 'no' is asked again on --resume."""
    with _io_lock:
        hit = _recall(key)
        if hit is not _MISSING:
            print(f"  [Approval] {title}: using your earlier answer ({'yes' if hit else 'no'})")
            return bool(hit)
        _banner(title, lines)
        if _state["auto"]:
            print("  [auto-approve] accepted")
            return True
        hint = "Y/n" if default else "y/N"
        while True:
            ans = _ask(f"  Approve? [{hint}]  (or type abort): ").lower()
            if ans == "":
                res = default
                break
            if ans in ("y", "yes"):
                res = True
                break
            if ans in ("n", "no"):
                res = False
                break
            print("  Please type y, n or abort.")
        if res or remember_no:
            _remember(key, res)
        return res


# --------------------------------------------------------------------------- pick from a list
def choose(key: str, title: str, items: Sequence[Tuple[str, str]], default_ids: Sequence[str],
           intro: Sequence[str] = ()) -> List[str]:
    """items = [(label, id)]. Returns the chosen ids. Items marked * are what the agent proposes."""
    ids = [i for _, i in items]
    with _io_lock:
        hit = _recall(key)
        if hit is not _MISSING:
            kept = [i for i in hit if i in ids]
            if kept or not hit:
                print(f"  [Approval] {title}: using your earlier selection ({len(kept)} item(s))")
                return kept
        default = [i for i in ids if i in set(default_ids)]
        _banner(title, list(intro))
        for n, (label, i) in enumerate(items, 1):
            print(f"  {'*' if i in default else ' '} {n:>2}. {label}")
        if _state["auto"]:
            print("  [auto-approve] kept the items marked *")
            return default
        while True:
            ans = _ask("  Numbers to KEEP (e.g. 1,3 or 2-4), 'all', 'none', Enter = keep the * ones: ").lower()
            if ans == "":
                chosen = default
                break
            if ans == "all":
                chosen = ids
                break
            if ans == "none":
                chosen = []
                break
            try:
                picked: List[int] = []
                for part in re.split(r"[,\s]+", ans):
                    if not part:
                        continue
                    if "-" in part:
                        a, b = part.split("-", 1)
                        picked += list(range(int(a), int(b) + 1))
                    else:
                        picked.append(int(part))
                if any(p < 1 or p > len(items) for p in picked):
                    raise ValueError
                chosen = [ids[p - 1] for p in dict.fromkeys(picked)]
                break
            except ValueError:
                print(f"  Please type numbers between 1 and {len(items)}, 'all', 'none' or abort.")
        _remember(key, chosen)
        return chosen


# --------------------------------------------------------------------------- edit a few fields
def edit_fields(key: str, title: str, fields: Dict[str, str], info: Sequence[str] = ()) -> Tuple[Dict[str, str], Set[str]]:
    """Show fields, let the human correct them with  field=new value . Returns (values, names_of_changed_fields)."""
    with _io_lock:
        hit = _recall(key)
        if hit is not _MISSING and isinstance(hit, dict) and "values" in hit:
            print(f"  [Approval] {title}: using your earlier answer")
            vals = dict(fields)
            vals.update({k: v for k, v in hit["values"].items() if k in fields})
            return vals, set(hit.get("changed", []))
        _banner(title, list(info))
        values = dict(fields)
        changed: Set[str] = set()
        for k, v in values.items():
            print(f"    {k:<24}= {v if v else '(empty)'}")
        if _state["auto"]:
            print("  [auto-approve] accepted as proposed")
            return values, changed
        while True:
            ans = _ask("  Correct something with  field=new value  (Enter = all correct, abort = stop): ")
            if not ans:
                break
            if "=" not in ans:
                print("  Format:  field=new value   e.g.  ticker=GOOGL")
                continue
            name, val = ans.split("=", 1)
            name = name.strip().lower()
            match = [k for k in values if k.lower() == name] or [k for k in values if k.lower().startswith(name)]
            if len(match) != 1:
                print(f"  Unknown or ambiguous field '{name}'. Fields: {', '.join(values)}")
                continue
            values[match[0]] = val.strip()
            changed.add(match[0])
            print(f"    {match[0]:<24}= {values[match[0]] or '(empty)'}")
        _remember(key, {"values": values, "changed": sorted(changed)})
        return values, changed


# --------------------------------------------------------------------------- approved-source registry
def _host(url: str) -> str:
    try:
        host = urlparse(url if "//" in url else "//" + url).netloc.lower()
    except Exception:
        return ""
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _base_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "gov", "ac", "edu"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def add_approved_urls(urls: Sequence[str]) -> None:
    """Remember the domains of sources the human approved (documents, web pages, SEC)."""
    with _lock:
        doms = set(_state["decisions"].get(_APPROVED_KEY, []))
        for u in urls:
            h = _host(u)
            if h:
                doms.add(_base_domain(h))
        _state["decisions"][_APPROVED_KEY] = sorted(doms)
        _save()


def approved_domains() -> Set[str]:
    with _lock:
        return set(_state["decisions"].get(_APPROVED_KEY, []))


def filter_web_results(results: List[Dict[str, str]], company_name: str, extra_names: Sequence[str] = ()) -> List[Dict[str, str]]:
    """Guard for the live web fallback used while writing sections.
    A result is kept only if (a) its domain was approved by the human (when any domain was approved) AND
    (b) its url/title/snippet mentions the company. Everything else is dropped, so another company's pages
    (or a Wikipedia/Yahoo fallback result about someone else) cannot become evidence."""
    toks: List[str] = []
    for name in [company_name, *extra_names]:
        for t in re.findall(r"[a-z0-9]+", (name or "").lower()):
            if len(t) > 2 and t not in _CORP and t not in toks:
                toks.append(t)
    domains = approved_domains()
    kept = []
    for r in results:
        href = r.get("href", "")
        blob = f"{href} {r.get('title', '')} {r.get('body', '')}".lower()
        domain_ok = (not domains) or (_base_domain(_host(href)) in domains)
        name_ok = (not toks) or any(t in blob for t in toks)
        if domain_ok and name_ok:
            kept.append(r)
    dropped = len(results) - len(kept)
    if dropped:
        print(f"  [Guard] ignored {dropped} web result(s): not from an approved source or not about '{company_name}'")
    return kept


# --------------------------------------------------------------------------- approved identity + name decisions
_IDENT_KEY = "_identity"
_ENT_KEY = "_entities"


def set_identity(names: Sequence[str], excluded: Sequence[str]) -> None:
    """Remember which company this run is about (names it is known by) and what must never be used."""
    with _lock:
        _state["decisions"][_IDENT_KEY] = {"names": [n for n in names if n], "excluded": [x for x in excluded if x]}
        _save()


def identity() -> Tuple[List[str], List[str]]:
    with _lock:
        d = _state["decisions"].get(_IDENT_KEY) or {}
        return list(d.get("names", [])), list(d.get("excluded", []))


def entity_statuses() -> Dict[str, str]:
    """{normalised organisation name: 'allowed' | 'forbidden'} decided by the human."""
    with _lock:
        return {k: v.get("status", "") for k, v in (_state["decisions"].get(_ENT_KEY) or {}).items()}


def forbidden_names() -> List[str]:
    with _lock:
        return [v.get("name", k) for k, v in (_state["decisions"].get(_ENT_KEY) or {}).items()
                if v.get("status") == "forbidden"]


def set_entity_status(core: str, status: str, display: str = "") -> None:
    with _lock:
        ents = _state["decisions"].setdefault(_ENT_KEY, {})
        ents[core] = {"status": status, "name": display or core}
        _save()


def select_one(key: str, title: str, lines: Sequence[str], options: Sequence[Tuple[str, str]], default: str) -> str:
    """Pick exactly one option. options = [(letter, description)]. Enter = default. Remembered for --resume."""
    letters = [o[0] for o in options]
    with _io_lock:
        hit = _recall(key)
        if hit is not _MISSING and hit in letters:
            print(f"  [Approval] {title}: using your earlier answer ({hit})")
            return hit
        _banner(title, lines)
        for letter, desc in options:
            print(f"    [{letter}] {desc}{'   <- Enter' if letter == default else ''}")
        if _state["auto"]:
            print(f"  [auto-approve] {default}")
            return default
        while True:
            ans = _ask(f"  Your choice [{'/'.join(letters)}] (Enter = {default}, or abort): ").lower()
            if ans == "":
                ans = default
            if ans in letters:
                break
            print(f"  Please type one of: {', '.join(letters)}")
        _remember(key, ans)
        return ans