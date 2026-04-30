"""
Skill 导入服务 —— 把一条 skill 详情页 URL 实物化到本地。

对外三个入口：`import_skill_from_url` / `probe_skill_url` / `refresh_bundle_skill`。
"""
from __future__ import annotations

import asyncio
import io
import re
import zipfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from loguru import logger

from models.skill import (
    BundleSkillConfig,
    BundleSkillSource,
    Skill,
    SkillCreate,
    SkillImportRequest,
)
from storage.skill_store import SkillStore

WORKSPACE_ROOT = Path(__file__).parent.parent.parent / "data" / "workspace"
SKILLS_DIR = WORKSPACE_ROOT / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

CLAWHUB_API_BASE = "https://agentskillhub.dev/api/v1"
SKILLHUB_CN_DOWNLOAD_URL = "https://lightmake.site/api/v1/download"

DEFAULT_TIMEOUT = 30.0


class SkillImportError(Exception):
    """对外抛给 router 再转 HTTP 400 的统一异常。"""


# ── URL 解析 ────────────────────────────────────────────────────────────────
# ClawHub: /<user>/<slug> 或 /u/<user>/skills/<slug>
# SkillHub: /skills/<slug>（全局唯一，没有 owner）
_CLAWHUB_FULL_RE = re.compile(r"^/u/(?P<user>[^/]+)/skills/(?P<slug>[^/?#]+)/?$")
_CLAWHUB_SHORT_RE = re.compile(r"^/(?P<user>[^/?#]+)/(?P<slug>[^/?#]+)/?$")
_SKILLHUB_RE = re.compile(r"^/skills?/(?P<slug>[^/?#]+)/?$")

# ClawHub 短链首段不能是平台保留路径
_CLAWHUB_RESERVED = {
    "u", "skills", "skill", "api", "docs", "about", "login", "signup",
    "sign-in", "sign-up", "search", "help", "pricing", "settings",
    "dashboard", "explore", "mcp", "mcps", "agents", "static", "assets", "_next",
}

# 两个官方元技能本地已有 builtin，遇到直接劝退
_OFFICIAL_BUILTIN_EQUIV = {
    "find-skills": "find_skill",
    "skill-creator": "skill_creator",
}


def _parse_url(url: str) -> dict[str, Optional[str]]:
    """识别 URL → {provider, username?, slug}。host 直接分叉。"""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if "clawhub.ai" in host or "agentskillhub" in host:
        m = _CLAWHUB_FULL_RE.match(path)
        if m:
            return {"provider": "clawhub", "username": m.group("user"), "slug": m.group("slug")}
        m = _CLAWHUB_SHORT_RE.match(path)
        if m and m.group("user").lower() not in _CLAWHUB_RESERVED:
            return {"provider": "clawhub", "username": m.group("user"), "slug": m.group("slug")}
        raise SkillImportError(
            f"无法从 {url} 解析 ClawHub 链接，"
            "支持格式：https://clawhub.ai/<user>/<slug> 或 /u/<user>/skills/<slug>"
        )

    if "skillhub.cn" in host or "skill-cn.com" in host:
        m = _SKILLHUB_RE.match(path)
        if not m:
            raise SkillImportError(
                f"无法从 {url} 解析 SkillHub 链接，支持格式：https://skillhub.cn/skills/<slug>"
            )
        slug = m.group("slug")
        if slug in _OFFICIAL_BUILTIN_EQUIV:
            raise SkillImportError(
                f"SkillHub 的 /{slug} 本地已有等价内置工具："
                f"在 Skill 池里找 slug=`{_OFFICIAL_BUILTIN_EQUIV[slug]}` 启用即可，无需导入。"
            )
        return {"provider": "skillhub_cn", "username": None, "slug": slug}

    raise SkillImportError(
        f"暂不支持的来源 {host}，目前支持 clawhub.ai / agentskillhub.dev / skillhub.cn"
    )


# ── 共用小工具 ──────────────────────────────────────────────────────────────
def _extract_skill_md_meta(skill_md: str) -> dict[str, str]:
    """从 SKILL.md 的 YAML frontmatter 抽 name / description / version。"""
    out: dict[str, str] = {}
    if not skill_md:
        return out
    lines = skill_md.splitlines()
    if not (lines and lines[0].strip() == "---"):
        return out
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip().lower()
            if k in ("name", "description", "version"):
                out[k] = v.strip().strip('"').strip("'")
    return out


def _extract_skill_md_summary(skill_md: str) -> str:
    """SKILL.md 描述优先用 frontmatter.description，否则第一段正文。"""
    meta = _extract_skill_md_meta(skill_md)
    if meta.get("description"):
        return meta["description"][:200]
    for line in skill_md.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and s != "---":
            return s[:200]
    return ""


# ── ClawHub ────────────────────────────────────────────────────────────────
async def _fetch_clawhub_skill(username: str, slug: str) -> dict[str, Any]:
    api = f"{CLAWHUB_API_BASE.rstrip('/')}/u/{username}/skills/{slug}"
    logger.info(f"[SkillImport][clawhub] GET {api}")
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(api, headers={"Accept": "application/json"})
    if resp.status_code == 404:
        raise SkillImportError(f"ClawHub 找不到 skill: {username}/{slug}")
    if resp.status_code >= 400:
        raise SkillImportError(f"ClawHub API {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as e:
        raise SkillImportError(f"ClawHub API 响应不是 JSON: {e}")


def _github_raw_url(source_identifier: str, default_branch: str, skill_path: str, file_path: str) -> str:
    rel = f"{skill_path}/{file_path}".replace("//", "/").strip("/")
    return f"https://raw.githubusercontent.com/{source_identifier}/{default_branch}/{rel}"


async def _download_file(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    resp = await client.get(url)
    if resp.status_code >= 400:
        raise SkillImportError(f"下载失败 ({resp.status_code}) {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)


async def _write_clawhub_bundle(
    username: str,
    slug: str,
    data: dict[str, Any],
    local_dir: Path,
) -> tuple[list[str], dict[str, Any]]:
    """落地 ClawHub 返回的 skillMdRaw + fileManifest；返回 (files, meta_for_config)。"""
    skill_info = data.get("skill") or {}
    version_info = data.get("latestVersion") or {}
    skill_md_raw: str = version_info.get("skillMdRaw") or ""
    manifest: list[dict] = version_info.get("fileManifest") or []

    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "SKILL.md").write_text(skill_md_raw, encoding="utf-8")

    source_identifier = skill_info.get("sourceIdentifier")
    default_branch = skill_info.get("defaultBranch") or "main"
    skill_path = skill_info.get("skillPath") or slug

    downloaded = ["SKILL.md"]
    if source_identifier:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            await asyncio.gather(
                *[
                    _download_file(
                        client,
                        _github_raw_url(source_identifier, default_branch, skill_path, entry["path"]),
                        local_dir / entry["path"],
                    )
                    for entry in manifest
                    if entry.get("path") and entry["path"].lower() != "skill.md"
                ],
                return_exceptions=False,
            )
        downloaded.extend(
            entry["path"] for entry in manifest
            if entry.get("path") and entry["path"].lower() != "skill.md"
        )
    else:
        logger.warning(
            f"[SkillImport][clawhub] {username}/{slug} 的 sourceIdentifier 为空，只保存了 SKILL.md"
        )

    return downloaded, {
        "name": skill_info.get("name") or slug,
        "description": skill_info.get("description") or "",
        "version": version_info.get("version"),
        "source_type": skill_info.get("sourceType"),
        "source_identifier": source_identifier,
        "default_branch": default_branch,
        "skill_path": skill_path,
        "skill_md_raw": skill_md_raw,
    }


# ── SkillHub.cn ────────────────────────────────────────────────────────────
# 按其 CLI 的规则：GET download?slug=<slug>（跟重定向）→ 拿到一个 zip → 解压即可。
async def _download_skillhub_zip(slug: str) -> bytes:
    """`lightmake.site/api/v1/download?slug=<slug>` 直接返回 zip（实际是 302 到 CDN）。"""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(SKILLHUB_CN_DOWNLOAD_URL, params={"slug": slug})
    if resp.status_code == 404:
        raise SkillImportError(f"SkillHub 找不到 skill: {slug}")
    if resp.status_code >= 400:
        raise SkillImportError(f"SkillHub 下载失败 HTTP {resp.status_code}")
    if resp.content[:2] != b"PK":
        raise SkillImportError(f"SkillHub 返回的不是 zip 包（slug={slug}）")
    return resp.content


def _extract_zip_to(zip_bytes: bytes, dest: Path) -> list[str]:
    """解压到 dest，防路径穿越；返回相对路径列表。"""
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    names: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = info.filename.lstrip("/")
            if ".." in Path(rel).parts:
                logger.warning(f"[SkillImport] 跳过可疑路径 {rel}")
                continue
            out = (dest / rel).resolve()
            if not str(out).startswith(str(dest_resolved)):
                logger.warning(f"[SkillImport] 跳过越界路径 {rel}")
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as dst:
                dst.write(src.read())
            names.append(rel)
    return names


async def _write_skillhub_cn_bundle(slug: str, local_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """下 zip → 解压 → 读 SKILL.md frontmatter 拿 name/description/version。"""
    zip_bytes = await _download_skillhub_zip(slug)
    files = _extract_zip_to(zip_bytes, local_dir)
    md = local_dir / "SKILL.md"
    skill_md_raw = md.read_text(encoding="utf-8") if md.exists() else ""
    meta = _extract_skill_md_meta(skill_md_raw)
    return files, {
        "name": meta.get("name") or slug,
        "description": meta.get("description") or "",
        "version": meta.get("version"),
        "skill_md_raw": skill_md_raw,
    }


# ── 对外入口 ───────────────────────────────────────────────────────────────
async def import_skill_from_url(request: SkillImportRequest) -> Skill:
    info = _parse_url(request.url)
    provider = info["provider"]
    slug = info["slug"]
    username = info["username"]
    assert provider and slug

    if provider == "clawhub":
        assert username
        local_slug = (request.override_slug or f"clawhub__{username}__{slug}").replace("/", "__")
        local_dir = SKILLS_DIR / local_slug
        data = await _fetch_clawhub_skill(username, slug)
        files, meta = await _write_clawhub_bundle(username, slug, data, local_dir)
        source = BundleSkillSource(
            provider="clawhub",
            url=request.url,
            username=username,
            slug=slug,
            version=meta.get("version"),
            source_type=meta.get("source_type"),
            source_identifier=meta.get("source_identifier"),
            default_branch=meta.get("default_branch"),
            skill_path=meta.get("skill_path"),
        )
    else:  # skillhub_cn
        local_slug = (request.override_slug or f"skillhub_cn__{slug}").replace("/", "__")
        local_dir = SKILLS_DIR / local_slug
        files, meta = await _write_skillhub_cn_bundle(slug, local_dir)
        source = BundleSkillSource(
            provider="skillhub_cn",
            url=request.url,
            username=None,
            slug=slug,
            version=meta.get("version"),
        )

    summary = _extract_skill_md_summary(meta.get("skill_md_raw") or "") or meta.get("description") or ""
    config = BundleSkillConfig(
        source=source,
        local_dir=str(local_dir.relative_to(WORKSPACE_ROOT.parent)),
        summary=summary,
        files=files,
    )

    store = SkillStore()
    if await store.get(local_slug):
        raise SkillImportError(f"本地已存在 slug={local_slug} 的 skill，若要覆盖请先删除或调 refresh")

    return await store.create(
        SkillCreate(
            slug=local_slug,
            name=meta.get("name") or local_slug,
            description=meta.get("description") or summary,
            category=request.category or "导入",
            enabled=True,
            kind="bundle",
            config=config.model_dump(),
        )
    )


async def probe_skill_url(url: str) -> dict[str, Any]:
    """贴 URL 时的轻量预览：只解析 URL，不做网络调用。

    之前尝试过"先查索引、miss 就下 zip"，索引只有 top 50、不查就要下包，
    复杂度远高于收益。保持最简：告诉前端 provider + slug + 本地会落成的 slug，
    用户点导入再真正下载。
    """
    info = _parse_url(url)
    provider = info["provider"]
    slug = info["slug"]
    username = info["username"]
    assert provider and slug

    if provider == "clawhub":
        assert username
        return {
            "provider": "clawhub",
            "username": username,
            "slug": slug,
            "suggested_slug": f"clawhub__{username}__{slug}",
        }
    return {
        "provider": "skillhub_cn",
        "username": None,
        "slug": slug,
        "suggested_slug": f"skillhub_cn__{slug}",
    }


async def refresh_bundle_skill(slug: str) -> Skill:
    """按 source 里记录的 provider + slug(+username) 重新拉取并覆盖本地文件。

    保留用户改过的 name / description / category，只刷 SKILL.md、资源文件、
    source.version / summary / files。
    """
    store = SkillStore()
    existing = await store.get(slug)
    if not existing:
        raise SkillImportError(f"skill 不存在: {slug}")
    if existing.kind != "bundle":
        raise SkillImportError(f"{slug} 不是 bundle 类型，不能刷新")

    cfg = BundleSkillConfig.model_validate(existing.config)
    src = cfg.source
    if src.provider not in ("clawhub", "skillhub_cn") or not src.slug:
        raise SkillImportError(
            f"{slug} 没有可追溯的远端来源（provider={src.provider}），无法刷新；请重新导入。"
        )

    local_dir = (WORKSPACE_ROOT.parent / cfg.local_dir).resolve()
    local_dir.mkdir(parents=True, exist_ok=True)

    if src.provider == "clawhub":
        if not src.username:
            raise SkillImportError(f"{slug} 缺少 clawhub username，无法刷新")
        data = await _fetch_clawhub_skill(src.username, src.slug)
        files, meta = await _write_clawhub_bundle(src.username, src.slug, data, local_dir)
        new_source = BundleSkillSource(
            provider="clawhub",
            url=src.url,
            username=src.username,
            slug=src.slug,
            version=meta.get("version"),
            source_type=meta.get("source_type") or src.source_type,
            source_identifier=meta.get("source_identifier") or src.source_identifier,
            default_branch=meta.get("default_branch") or src.default_branch,
            skill_path=meta.get("skill_path") or src.skill_path,
        )
    else:  # skillhub_cn
        files, meta = await _write_skillhub_cn_bundle(src.slug, local_dir)
        new_source = BundleSkillSource(
            provider="skillhub_cn",
            url=src.url,
            username=None,
            slug=src.slug,
            version=meta.get("version") or src.version,
        )

    summary = _extract_skill_md_summary(meta.get("skill_md_raw") or "") or existing.description

    new_config = BundleSkillConfig(
        source=new_source,
        local_dir=cfg.local_dir,
        summary=summary,
        files=files,
    )
    from models.skill import SkillUpdate  # 延迟导入避免循环
    return await store.update(slug, SkillUpdate(config=new_config.model_dump()))


async def read_skill_md(slug: str) -> dict[str, Any]:
    """读取 bundle skill 的 SKILL.md 正文（前端预览用）。"""
    store = SkillStore()
    existing = await store.get(slug)
    if not existing:
        raise SkillImportError(f"skill 不存在: {slug}")
    if existing.kind != "bundle":
        raise SkillImportError(f"{slug} 不是 bundle 类型")
    cfg = BundleSkillConfig.model_validate(existing.config)
    md_path = (WORKSPACE_ROOT.parent / cfg.local_dir / "SKILL.md").resolve()
    if not md_path.exists():
        raise SkillImportError(f"{slug} 的 SKILL.md 丢失: {md_path}")
    return {
        "slug": slug,
        "local_dir": cfg.local_dir,
        "content": md_path.read_text(encoding="utf-8"),
        "files": cfg.files,
    }


# ── 给 skill_creator 用的：落地一个本地 skill 包 ──────────────────────────
async def register_local_bundle(
    slug: str,
    name: str,
    description: str,
    category: str,
    skill_md: str,
    extra_files: Optional[dict[str, str]] = None,
) -> Skill:
    """skill_creator 直接把 LLM 生成的 SKILL.md 落地并注册。"""
    local_slug = slug.replace("/", "__")
    local_dir = SKILLS_DIR / f"local__{local_slug}"
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    written_files = ["SKILL.md"]
    for fname, content in (extra_files or {}).items():
        safe = Path(fname).name  # 防穿越
        (local_dir / safe).write_text(content, encoding="utf-8")
        written_files.append(safe)

    summary = _extract_skill_md_summary(skill_md) or description

    config = BundleSkillConfig(
        source=BundleSkillSource(provider="local"),
        local_dir=str(local_dir.relative_to(WORKSPACE_ROOT.parent)),
        summary=summary,
        files=written_files,
    )

    store = SkillStore()
    if await store.get(local_slug):
        raise SkillImportError(f"slug={local_slug} 已存在，建议换一个名字或先删除旧的")

    return await store.create(
        SkillCreate(
            slug=local_slug,
            name=name,
            description=description or summary,
            category=category or "自定义",
            enabled=True,
            kind="bundle",
            config=config.model_dump(),
        )
    )


__all__ = [
    "SkillImportError",
    "import_skill_from_url",
    "probe_skill_url",
    "refresh_bundle_skill",
    "read_skill_md",
    "register_local_bundle",
]
