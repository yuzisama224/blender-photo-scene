"""Check skill metadata and its local reference closure."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

import yaml


def check(root):
    problems = []
    skill = root / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    front = re.match(r"\A---\n(.*?)\n---(?:\n|$)", text, flags=re.S)
    metadata = yaml.safe_load(front.group(1)) if front else {}
    if not isinstance(metadata, dict) or metadata.get("name") != root.name:
        problems.append("技能 name 必须与安装目录名一致")
    if not metadata or not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
        problems.append("缺少非空 description")
    interface = yaml.safe_load((root / "agents/openai.yaml").read_text(encoding="utf-8"))
    if "$" + root.name not in interface["interface"]["default_prompt"]:
        problems.append("default_prompt 缺少显式技能调用")
    if not interface.get("policy", {}).get("allow_implicit_invocation", True):
        problems.append("此技能应支持自动发现")
    links = 0
    for doc in root.rglob("*.md"):
        if any(part.startswith(".") for part in doc.relative_to(root).parts):
            continue
        content = doc.read_text(encoding="utf-8")
        for raw in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            target = raw.strip().split(' "', 1)[0].strip("<>")
            if urlparse(target).scheme or target.startswith("#"):
                continue
            target = unquote(target.split("#", 1)[0])
            path = (doc.parent / target).resolve()
            links += 1
            if not path.is_relative_to(root) or not path.exists():
                problems.append(f"无效本地引用 {doc.relative_to(root)} → {target}")
    scripts = list((root / "scripts").glob("*.py"))
    for script in scripts:
        try:
            ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        except SyntaxError as error:
            problems.append(f"{script.name}: {error}")
    provenance = root / "UPSTREAM.json"
    if not provenance.is_file():
        problems.append("缺少上游来源清单")
    else:
        sources = json.loads(provenance.read_text(encoding="utf-8")).get("sources", [])
        if len(sources) != 4:
            problems.append("来源清单应登记四个上游项目")
        for source in sources:
            if not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
                problems.append(f"{source['id']} 缺少固定提交")
            license_file = (root / source["license"]["local_path"]).resolve()
            if not license_file.is_relative_to(root) or not license_file.is_file():
                problems.append(f"{source['id']} 缺少本地许可证")
            elif hashlib.sha256(license_file.read_bytes()).hexdigest() != source["license"]["sha256"]:
                problems.append(f"{source['id']} 许可证与来源校验值不一致")
            for entry in source["files"]:
                local = (root / entry["local_path"]).resolve()
                if not local.is_relative_to(root) or not local.is_file():
                    problems.append(f"缺少已登记文件 {entry['local_path']}")
                for origin in entry["source_files"]:
                    if source["commit"] not in origin["url"] or not re.fullmatch(r"[0-9a-f]{64}", origin["sha256"]):
                        problems.append(f"来源记录未固定 {origin['path']}")
    if len(list((root / "licenses").glob("*"))) != 4:
        problems.append("应保留四个上游项目的许可全文")
    return {"pass": not problems, "local_links_checked": links, "scripts_parsed": len(scripts), "issues": problems}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = check(args.root.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
