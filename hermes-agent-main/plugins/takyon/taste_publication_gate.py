"""Deterministic publication gate for Taste-authored public landings.

The gate intentionally owns no browser, provider credential, or paid-provider
call.  A renderer evaluates :data:`TASTE_RENDER_INSPECTION_JS` at 1440x900 and
390x844 and passes the resulting facts here.  Generated-image inspection is an
injected callback/result so production can keep that work on a Safebox-minted,
money-gated capability without ever exposing a provider key to this module.

Integration order:

1. The initial Taste worker writes ``DESIGN.md`` and generates its assets.
2. The bounded renderer captures both required screenshots and evaluates the
   inspection script.
3. ``validate_taste_publication`` runs before a publish pointer can advance.
4. A successful initial pass persists ``capture_design_snapshot`` with
   ``write_design_snapshot``.  Every later product worker validates against the
   snapshot, preventing an app-workflow pass from silently diluting the landing.

The rules below are mechanical checks from the canonical Taste preflight.  They
do not prescribe a palette, layout family, typeface, or final visual template.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import struct
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlparse


DESKTOP_VIEWPORT = (1440, 900)
MOBILE_VIEWPORT = (390, 844)
DESIGN_SNAPSHOT_RELATIVE_PATH = ".takyon/taste-design-snapshot.json"


# One id per checkbox in the canonical Taste Final Pre-Flight Check.  Rules that
# are contextual still require an explicit pass/N/A explanation; they are never
# silently skipped because a mechanical probe cannot infer the brief.
CANONICAL_PREFLIGHT_IDS = (
    "brief_inference",
    "dial_values",
    "design_system",
    "redesign_audit",
    "zero_dashes",
    "theme_lock",
    "color_lock",
    "shape_lock",
    "button_contrast",
    "cta_button_wrap",
    "form_contrast",
    "serif_discipline",
    "premium_consumer_palette",
    "italic_descender_clearance",
    "hero_fits_viewport",
    "hero_top_padding",
    "hero_stack_discipline",
    "eyebrow_count",
    "split_header_ban",
    "zigzag_alternation_cap",
    "duplicate_cta_intent",
    "logo_wall_logo_only",
    "bento_background_diversity",
    "trusted_logo_wall_placement",
    "copy_self_audit",
    "motion_motivated",
    "marquee_limit",
    "navigation_single_line",
    "section_layout_repetition",
    "bento_cell_count",
    "long_list_component",
    "real_images",
    "image_overlay_labels",
    "photo_credit_captions",
    "version_footers",
    "micro_meta_sentences",
    "hero_decoration_strip",
    "floating_section_subtext",
    "progress_bar_tracks",
    "locale_time_weather",
    "scroll_cues",
    "hero_version_labels",
    "section_numbering_eyebrows",
    "decorative_dots",
    "row_border_repetition",
    "content_density",
    "quote_length",
    "motion_claimed_shown",
    "gsap_patterns",
    "scroll_listener_ban",
    "reduced_motion",
    "dark_mode",
    "mobile_collapse",
    "viewport_stability",
    "effect_cleanup",
    "ui_states",
    "card_restraint",
    "icon_family",
    "motion_isolation",
    "ai_tells",
    "core_web_vitals",
    "one_design_system",
)


# This script is deliberately data-only.  The browser runner owns process
# lifecycle and screenshots; the gate owns policy.  Keeping the probe here makes
# the renderer and publication path consume one versioned inspection contract.
TASTE_RENDER_INSPECTION_JS = r"""() => {
  const visible = (element) => {
    if (!(element instanceof Element)) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
  };
  const lineCount = (element) => {
    if (!element) return 0;
    const tops = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!String(node.textContent || "").trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const rect of range.getClientRects()) {
        if (rect.width > 0 && rect.height > 0) tops.push(Math.round(rect.top));
      }
    }
    return [...new Set(tops)].length;
  };
  const text = (element) => String(element?.innerText || element?.textContent || "")
    .replace(/\s+/g, " ").trim();
  const colorTuple = (value) => {
    const match = String(value || "").match(/rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)(?:\D+([\d.]+))?/i);
    return match ? [Number(match[1]), Number(match[2]), Number(match[3]),
      match[4] === undefined ? 1 : Number(match[4])] : null;
  };
  const effectiveBackground = (element) => {
    let current = element;
    while (current instanceof Element) {
      const value = getComputedStyle(current).backgroundColor;
      const tuple = colorTuple(value);
      if (tuple && tuple[3] > 0.05) return value;
      current = current.parentElement;
    }
    return getComputedStyle(document.body).backgroundColor;
  };
  const themeMode = (value) => {
    const tuple = colorTuple(value);
    if (!tuple) return "unknown";
    const channels = tuple.slice(0, 3).map((channel) => {
      const normalized = channel / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2] >= 0.42
      ? "light" : "dark";
  };
  const imagePath = (source) => {
    try {
      const url = new URL(source, location.href);
      return url.origin === location.origin ? `${url.pathname}${url.search}` : url.href;
    } catch { return String(source || ""); }
  };
  const layoutFamily = (section) => {
    const images = [...section.querySelectorAll("img")].filter(visible);
    const heading = section.querySelector("h1, h2, h3");
    if (images.length > 1) return "gallery";
    if (images.length === 1 && heading) {
      const imageRect = images[0].getBoundingClientRect();
      const headingRect = heading.getBoundingClientRect();
      const overlap = Math.min(imageRect.bottom, headingRect.bottom) - Math.max(imageRect.top, headingRect.top);
      if (overlap > Math.min(imageRect.height, headingRect.height) * 0.2) return "media-split";
      return "media-stack";
    }
    if (section.querySelector("blockquote")) return "quote";
    if (section.querySelector("form")) return "form";
    const peers = [...section.querySelectorAll(":scope > *, :scope > * > *")].filter(visible)
      .map((element) => element.getBoundingClientRect()).filter((rect) => rect.width > 40 && rect.height > 40);
    const columns = [...new Set(peers.map((rect) => Math.round(rect.left / 20) * 20))];
    if (columns.length >= 3) return "multi-column";
    const headingRect = heading?.getBoundingClientRect() || null;
    if (headingRect && Math.abs(headingRect.left + headingRect.width / 2 - innerWidth / 2) < innerWidth * 0.08) {
      return "centered-text";
    }
    return "text-stack";
  };
  const h1 = document.querySelector("main h1") || document.querySelector("h1");
  const hero = h1?.closest("section") || h1?.closest("main") || document.querySelector("main");
  const heroParagraphs = hero ? [...hero.querySelectorAll("p")].filter(visible) : [];
  const heroSubtext = heroParagraphs.find((paragraph) =>
    h1 && Boolean(h1.compareDocumentPosition(paragraph) & Node.DOCUMENT_POSITION_FOLLOWING)
  ) || null;
  const heroCtas = hero ? [...hero.querySelectorAll("a, button")].filter(visible) : [];
  const allCtas = [...document.querySelectorAll("a, button")].filter(visible).map((element) => ({
    label: text(element),
    line_count: lineCount(element),
    zone: element.closest("header") ? "header" : element.closest("footer") ? "footer" :
      element.closest("section") === hero ? "hero" : "main",
  })).filter((entry) => entry.label);
  const lastHeroCta = heroCtas.at(-1) || null;
  const priceAfterCta = hero && lastHeroCta ? [...hero.querySelectorAll("p, small, span")]
    .filter((element) => visible(element) &&
      Boolean(lastHeroCta.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING) &&
      /(?:[$€£]\s*\d|\d\s*(?:\/|per)\s*(?:month|year)|monthly|annually)/i.test(text(element)))
    .map(text) : [];
  const sections = [...document.querySelectorAll("main section")].filter(visible);
  const sectionLayouts = sections.map((section, index) => ({
    key: section.getAttribute("data-taste-section") || section.id || `section-${index + 1}`,
    family: layoutFamily(section),
    image_srcs: [...section.querySelectorAll("img")].filter(visible)
      .map((image) => imagePath(image.currentSrc || image.src)),
    theme: themeMode(effectiveBackground(section)),
  }));
  const eyebrowElements = [...document.querySelectorAll("main section *")].filter((element) => {
    if (!visible(element) || element.matches("a, button")) return false;
    const value = text(element);
    if (!value || value.length > 60 || element.children.length > 0) return false;
    const style = getComputedStyle(element);
    const spacing = Number.parseFloat(style.letterSpacing || "0");
    const size = Number.parseFloat(style.fontSize || "99");
    const uppercase = style.textTransform === "uppercase" ||
      (/[A-Z]/.test(value) && value === value.toUpperCase());
    return uppercase && spacing >= 1.1 && size <= 16;
  });
  const decorativeDots = [...document.querySelectorAll("main *")].filter((element) => {
    if (!visible(element) || text(element) || element.children.length) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2 || rect.width > 16 || rect.height > 16) return false;
    if (Math.abs(rect.width - rect.height) > 1) return false;
    const style = getComputedStyle(element);
    if (Number.parseFloat(style.borderRadius || "0") < rect.width * 0.45) return false;
    const semantic = element.closest('[role="status"], [role="alert"], [aria-live]') ||
      element.getAttribute("aria-label") || element.getAttribute("title");
    return !semantic && style.backgroundColor !== "rgba(0, 0, 0, 0)" &&
      style.backgroundColor !== "transparent";
  });
  const genericStepParents = new Set();
  for (const element of document.querySelectorAll("main *")) {
    if (!/^\s*(?:step|stage|phase|pass)\s*(?:\d+|one|two|three)\s*$/i.test(text(element))) continue;
    let card = element.parentElement;
    for (let depth = 0; card && depth < 3; depth += 1, card = card.parentElement) {
      const parent = card.parentElement;
      if (!parent || parent.children.length < 3) continue;
      const rects = [...parent.children].map((child) => child.getBoundingClientRect());
      const equal = rects.every((rect) =>
        Math.abs(rect.width - rects[0].width) <= 3 && Math.abs(rect.height - rects[0].height) <= 3
      );
      if (equal) genericStepParents.add(parent);
    }
  }
  const headers = [...document.querySelectorAll("body header")].filter(visible);
  const mains = [...document.querySelectorAll("body main")].filter(visible);
  const navigationItems = headers[0] ? [...headers[0].querySelectorAll("nav a, nav button")].filter(visible) : [];
  const accentColors = new Set();
  for (const element of document.querySelectorAll("a, button, [data-taste-accent]")) {
    if (!visible(element)) continue;
    const style = getComputedStyle(element);
    for (const value of [style.color, style.backgroundColor, style.borderColor]) {
      const tuple = colorTuple(value);
      if (tuple && tuple[3] > 0.1 && Math.max(...tuple.slice(0, 3)) - Math.min(...tuple.slice(0, 3)) >= 38) {
        accentColors.add(`rgb(${tuple[0]}, ${tuple[1]}, ${tuple[2]})`);
      }
    }
  }
  const radiusValues = (selector) => [...new Set([...document.querySelectorAll(selector)].filter(visible)
    .map((element) => Math.round(Number.parseFloat(getComputedStyle(element).borderRadius || "0"))))];
  const heroRect = hero?.getBoundingClientRect() || null;
  const h1Rect = h1?.getBoundingClientRect() || null;
  const heroImage = hero ? [...hero.querySelectorAll("img")].find(visible) : null;
  const imageRect = heroImage?.getBoundingClientRect() || null;
  const heroLayout = h1Rect && imageRect && Math.abs(h1Rect.top - imageRect.top) < Math.max(h1Rect.height, 180)
    ? "split" : h1Rect && Math.abs((h1Rect.left + h1Rect.width / 2) - innerWidth / 2) < innerWidth * 0.08
      ? "centered" : "stacked";
  const primaryCta = heroCtas[0] || null;
  const primaryRect = primaryCta?.getBoundingClientRect() || null;
  const nextSection = hero?.nextElementSibling instanceof Element ? hero.nextElementSibling : null;
  return {
    viewport_width: innerWidth,
    viewport_height: innerHeight,
    body_text: text(document.body),
    h1_line_count: lineCount(h1),
    hero_heading_text: text(h1),
    hero_subtext: text(heroSubtext),
    hero_subtext_line_count: lineCount(heroSubtext),
    hero_price_teasers_after_cta: priceAfterCta,
    eyebrow_count: eyebrowElements.length,
    eyebrow_labels: eyebrowElements.map(text),
    section_count: sections.length,
    section_layouts: sectionLayouts,
    decorative_dot_count: decorativeDots.length,
    generic_equal_step_group_count: genericStepParents.size,
    ctas: allCtas,
    image_srcs: [...document.images].filter(visible).map((image) => image.currentSrc || image.src),
    header_count: headers.length,
    main_count: mains.length,
    nested_main_count: document.querySelectorAll("main main").length,
    document_width: document.documentElement.clientWidth,
    scroll_width: document.documentElement.scrollWidth,
    header_width: headers[0]?.getBoundingClientRect().width || 0,
    navigation_height: headers[0]?.getBoundingClientRect().height || 0,
    navigation_line_count: new Set(navigationItems.map((item) => Math.round(item.getBoundingClientRect().top))).size,
    hero_width: heroRect?.width || 0,
    hero_layout: heroLayout,
    hero_heading_top_ratio: h1Rect ? h1Rect.top / innerHeight : null,
    primary_cta_visible: Boolean(primaryRect && primaryRect.top >= 0 && primaryRect.bottom <= innerHeight),
    hero_complete: Boolean(h1 && heroSubtext && primaryCta && visible(h1) && visible(heroSubtext)),
    next_section_intrusion: Boolean(nextSection && nextSection.getBoundingClientRect().top < innerHeight - 2),
    theme_modes: [...new Set(sectionLayouts.map((entry) => entry.theme).filter((mode) => mode !== "unknown"))],
    accent_colors: [...accentColors],
    shape_radii: {
      interactive: radiusValues('button, a[role="button"], a[class*="rounded"]'),
      cards: radiusValues("[data-taste-card], article"),
    },
    body_font_family: getComputedStyle(document.body).fontFamily,
    body_background_color: getComputedStyle(document.body).backgroundColor,
  };
}"""


@dataclass(frozen=True)
class GateFinding:
    code: str
    message: str
    path: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class PreflightEvidenceItem:
    passed: bool
    evidence: str
    source: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PreflightEvidenceItem":
        return cls(
            passed=bool(value.get("passed")),
            evidence=_normalize_text(str(value.get("evidence") or "")),
            source=_normalize_text(str(value.get("source") or "")),
        )


@dataclass(frozen=True)
class RenderInspection:
    width: int
    height: int
    screenshot_path: str
    screenshot_sha256: str
    inspected: bool
    probe: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RenderInspection":
        return cls(
            width=int(value.get("width") or value.get("viewport_width") or 0),
            height=int(value.get("height") or value.get("viewport_height") or 0),
            screenshot_path=str(value.get("screenshot_path") or ""),
            screenshot_sha256=str(value.get("screenshot_sha256") or ""),
            inspected=bool(value.get("inspected")),
            probe=value.get("probe") if isinstance(value.get("probe"), Mapping) else {},
        )


@dataclass(frozen=True)
class AssetVisualInspection:
    public_path: str
    image_sha256: str
    inspected: bool
    inspected_width: int = 0
    inspected_height: int = 0
    detected_text: tuple[str, ...] = ()
    fake_ui_detected: bool = False
    artifact_labels: tuple[str, ...] = ()
    source: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AssetVisualInspection":
        return cls(
            public_path=str(value.get("public_path") or ""),
            image_sha256=str(value.get("image_sha256") or ""),
            inspected=bool(value.get("inspected")),
            inspected_width=int(value.get("inspected_width") or 0),
            inspected_height=int(value.get("inspected_height") or 0),
            detected_text=tuple(str(item) for item in value.get("detected_text") or ()),
            fake_ui_detected=bool(value.get("fake_ui_detected")),
            artifact_labels=tuple(str(item) for item in value.get("artifact_labels") or ()),
            source=str(value.get("source") or ""),
        )


class SafeboxAssetInspector(Protocol):
    """Key-free integration seam for an independently authorized inspector.

    Implementations receive only a local image path and its non-secret receipt.
    A production implementation must obtain any paid capability from Safebox;
    this protocol must never be implemented by reading provider keys locally.
    """

    def __call__(self, image_path: Path, receipt: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class TasteDesignSnapshot:
    version: int
    design_sha256: str
    landing_sha256: str
    tokens_sha256: str
    design_read_sha256: str
    foundation_sha256: str
    dials: Mapping[str, int]
    tokens: Mapping[str, str]
    assets: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TasteDesignSnapshot":
        return cls(
            version=int(value.get("version") or 0),
            design_sha256=str(value.get("design_sha256") or ""),
            landing_sha256=str(value.get("landing_sha256") or ""),
            tokens_sha256=str(value.get("tokens_sha256") or ""),
            design_read_sha256=str(value.get("design_read_sha256") or ""),
            foundation_sha256=str(value.get("foundation_sha256") or ""),
            dials={str(k): int(v) for k, v in dict(value.get("dials") or {}).items()},
            tokens={str(k): str(v) for k, v in dict(value.get("tokens") or {}).items()},
            assets={str(k): str(v) for k, v in dict(value.get("assets") or {}).items()},
        )


@dataclass(frozen=True)
class TasteGateResult:
    findings: tuple[GateFinding, ...]
    snapshot: TasteDesignSnapshot | None

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def blocker(self) -> str:
        return "" if self.passed else "; ".join(f"{item.code}: {item.message}" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blocker": self.blocker,
            "findings": [item.to_dict() for item in self.findings],
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
        }


def _bounded_text(path: Path, *, limit: int = 512 * 1024) -> str:
    if not path.is_file():
        raise ValueError(f"required file is missing: {path}")
    if path.stat().st_size > limit:
        raise ValueError(f"required file exceeds {limit} bytes: {path}")
    return path.read_text(encoding="utf-8")


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _digest_text(value: str) -> str:
    return _digest_bytes(value.encode("utf-8"))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _markdown_section(content: str, title: str) -> str:
    match = re.search(
        rf"(?ims)^#{{1,6}}\s+{re.escape(title)}\s*$\n(.*?)(?=^#{{1,6}}\s|\Z)",
        content,
    )
    return str(match.group(1) if match else "").strip()


def _parse_design(content: str) -> tuple[str, str, dict[str, int]]:
    design_read = _markdown_section(content, "Design Read")
    foundation = _markdown_section(content, "Foundation")
    dials: dict[str, int] = {}
    for name in ("DESIGN_VARIANCE", "MOTION_INTENSITY", "VISUAL_DENSITY"):
        match = re.search(rf"(?im)^.*\b{re.escape(name)}\b.*?(\d{{1,2}})\b", content)
        if match:
            dials[name] = int(match.group(1))
    return design_read, foundation, dials


def _parse_tokens(content: str) -> dict[str, str]:
    return {
        name.strip(): value.strip()
        for name, value in re.findall(r"(--[a-zA-Z0-9_-]+)\s*:\s*([^;}{]+);", content)
    }


def _normalize_section_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _parse_image_plan(content: str) -> dict[str, tuple[str, ...]]:
    section = _markdown_section(content, "Image Plan")
    plan: dict[str, tuple[str, ...]] = {}
    for line in section.splitlines():
        match = re.match(r"^\s*[-*]\s+([^:]+):\s*(.+?)\s*$", line)
        if not match:
            continue
        key = _normalize_section_key(match.group(1))
        value = match.group(2).strip()
        if re.fullmatch(r"(?i)(?:none|no image|text only|n/a)", value):
            plan[key] = ()
            continue
        sources = re.findall(r"https?://[^\s,;)]+|/[a-zA-Z0-9_./?=&%-]+", value)
        plan[key] = tuple(dict.fromkeys(sources))
    return plan


def _preflight_findings(
    evidence: Mapping[str, PreflightEvidenceItem | Mapping[str, Any]],
) -> list[GateFinding]:
    normalized: dict[str, PreflightEvidenceItem] = {}
    for item_id, value in evidence.items():
        if isinstance(value, PreflightEvidenceItem):
            normalized[str(item_id)] = value
        elif isinstance(value, Mapping):
            normalized[str(item_id)] = PreflightEvidenceItem.from_mapping(value)
    missing = [item_id for item_id in CANONICAL_PREFLIGHT_IDS if item_id not in normalized]
    findings: list[GateFinding] = []
    if missing:
        findings.append(
            GateFinding(
                "preflight_evidence_missing",
                "canonical Taste preflight evidence is incomplete",
                evidence={"missing": missing},
            )
        )
    for item_id in CANONICAL_PREFLIGHT_IDS:
        item = normalized.get(item_id)
        if item is None:
            continue
        if not item.passed:
            findings.append(
                GateFinding(
                    "preflight_check_failed",
                    f"canonical Taste preflight failed: {item_id}",
                    evidence={"id": item_id, "detail": item.evidence, "source": item.source},
                )
            )
        if not item.evidence or not item.source:
            findings.append(
                GateFinding(
                    "preflight_evidence_invalid",
                    f"canonical Taste preflight lacks code/copy evidence: {item_id}",
                    evidence={"id": item_id},
                )
            )
    return findings


def _rgb_hue(value: str) -> float | None:
    match = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", value)
    if not match:
        return None
    red, green, blue = (int(channel) / 255 for channel in match.groups())
    high, low = max(red, green, blue), min(red, green, blue)
    if high == low:
        return None
    delta = high - low
    if high == red:
        hue = ((green - blue) / delta) % 6
    elif high == green:
        hue = (blue - red) / delta + 2
    else:
        hue = (red - green) / delta + 4
    return hue * 60


def _hue_cluster_count(colors: Sequence[str], *, tolerance: float = 28) -> int:
    clusters: list[float] = []
    for hue in (value for value in (_rgb_hue(color) for color in colors) if value is not None):
        if any(min(abs(hue - center), 360 - abs(hue - center)) <= tolerance for center in clusters):
            continue
        clusters.append(hue)
    return len(clusters)


def _png_dimensions_and_sha(path: Path) -> tuple[int, int, str]:
    data = path.read_bytes()
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise ValueError("invalid PNG dimensions")
    return width, height, _digest_bytes(data)


def _asset_receipts(root: Path) -> tuple[list[dict[str, Any]], list[GateFinding]]:
    receipts_dir = root / ".takyon" / "site-images"
    findings: list[GateFinding] = []
    assets: list[dict[str, Any]] = []
    if not receipts_dir.is_dir():
        return [], [GateFinding("asset_receipts_missing", "generated-image receipts are missing", str(receipts_dir))]
    for receipt_path in sorted(receipts_dir.glob("*.json")):
        try:
            receipt = json.loads(_bounded_text(receipt_path, limit=256 * 1024))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            findings.append(GateFinding("asset_receipt_invalid", str(exc), str(receipt_path)))
            continue
        if not isinstance(receipt, Mapping) or not receipt.get("success"):
            findings.append(GateFinding("asset_receipt_unsuccessful", "receipt is not successful", str(receipt_path)))
            continue
        public_path = str(receipt.get("public_path") or "")
        match = re.fullmatch(r"/generated/([a-z0-9]+(?:-[a-z0-9]+)*)\.png", public_path)
        if not match:
            findings.append(GateFinding("asset_public_path_invalid", public_path or "missing path", str(receipt_path)))
            continue
        image_path = root / "public" / "generated" / f"{match.group(1)}.png"
        try:
            width, height, image_sha = _png_dimensions_and_sha(image_path)
        except (OSError, ValueError) as exc:
            findings.append(GateFinding("asset_png_invalid", str(exc), str(image_path)))
            continue
        assets.append(
            {
                "public_path": public_path,
                "path": image_path,
                "receipt_path": receipt_path,
                "receipt": dict(receipt),
                "sha256": image_sha,
                "width": width,
                "height": height,
            }
        )
    if len(assets) < 2:
        findings.append(
            GateFinding(
                "asset_count_invalid",
                "Taste v2 requires at least two distinct real landing images when image generation is available",
                str(receipts_dir),
                {"count": len(assets), "minimum": 2},
            )
        )
    if len({asset["sha256"] for asset in assets}) != len(assets):
        findings.append(GateFinding("asset_duplicate_pixels", "generated assets are not visually distinct", str(receipts_dir)))
    return assets, findings


def capture_design_snapshot(workspace_path: str | Path) -> TasteDesignSnapshot:
    root = Path(workspace_path).resolve()
    design = _bounded_text(root / "DESIGN.md")
    landing = _bounded_text(root / "src" / "screens" / "landing.tsx")
    tokens_source = _bounded_text(root / "src" / "tokens.css")
    design_read, foundation, dials = _parse_design(design)
    assets, findings = _asset_receipts(root)
    if findings:
        raise ValueError("; ".join(item.message for item in findings))
    return TasteDesignSnapshot(
        version=1,
        design_sha256=_digest_text(design),
        landing_sha256=_digest_text(landing),
        tokens_sha256=_digest_text(tokens_source),
        design_read_sha256=_digest_text(_normalize_text(design_read)),
        foundation_sha256=_digest_text(_normalize_text(foundation)),
        dials=dials,
        tokens=_parse_tokens(tokens_source),
        assets={str(asset["public_path"]): str(asset["sha256"]) for asset in assets},
    )


def write_design_snapshot(workspace_path: str | Path, snapshot: TasteDesignSnapshot) -> Path:
    root = Path(workspace_path).resolve()
    target = root / DESIGN_SNAPSHOT_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def load_design_snapshot(workspace_path: str | Path) -> TasteDesignSnapshot | None:
    path = Path(workspace_path).resolve() / DESIGN_SNAPSHOT_RELATIVE_PATH
    if not path.is_file():
        return None
    value = json.loads(_bounded_text(path, limit=256 * 1024))
    if not isinstance(value, Mapping):
        raise ValueError("Taste design snapshot must be an object")
    return TasteDesignSnapshot.from_mapping(value)


def run_asset_visual_inspections(
    workspace_path: str | Path,
    inspector: SafeboxAssetInspector,
) -> dict[str, AssetVisualInspection]:
    """Inspect local assets through an injected key-free/Safebox-authorized seam."""

    root = Path(workspace_path).resolve()
    assets, findings = _asset_receipts(root)
    if findings:
        raise ValueError("; ".join(item.message for item in findings))
    results: dict[str, AssetVisualInspection] = {}
    for asset in assets:
        raw = inspector(Path(asset["path"]), dict(asset["receipt"]))
        if not isinstance(raw, Mapping):
            raise ValueError("asset inspector must return an object")
        merged = {
            **dict(raw),
            "public_path": asset["public_path"],
            "image_sha256": asset["sha256"],
        }
        results[str(asset["public_path"])] = AssetVisualInspection.from_mapping(merged)
    return results


def _render_findings(
    inspection: RenderInspection,
    *,
    expected: tuple[int, int],
    label: str,
    design: str,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    if (inspection.width, inspection.height) != expected:
        findings.append(
            GateFinding(
                "render_viewport_invalid",
                f"{label} render must be {expected[0]}x{expected[1]}",
                inspection.screenshot_path,
                {"actual": [inspection.width, inspection.height]},
            )
        )
    screenshot = Path(inspection.screenshot_path) if inspection.screenshot_path else None
    if not inspection.inspected or not inspection.probe:
        findings.append(GateFinding("render_not_inspected", f"{label} screenshot has no inspected browser facts"))
    if screenshot is None or not screenshot.is_file():
        findings.append(GateFinding("render_screenshot_missing", f"{label} screenshot is missing", inspection.screenshot_path))
    else:
        try:
            width, height, image_sha = _png_dimensions_and_sha(screenshot)
            if (width, height) != expected:
                findings.append(
                    GateFinding(
                        "render_png_dimensions_invalid",
                        f"{label} PNG has the wrong dimensions",
                        str(screenshot),
                        {"actual": [width, height], "expected": list(expected)},
                    )
                )
            if not inspection.screenshot_sha256 or inspection.screenshot_sha256 != image_sha:
                findings.append(GateFinding("render_digest_invalid", f"{label} screenshot digest is missing or stale", str(screenshot)))
        except (OSError, ValueError) as exc:
            findings.append(GateFinding("render_png_invalid", str(exc), str(screenshot)))
    probe = inspection.probe
    if int(probe.get("viewport_width") or 0) != expected[0] or int(probe.get("viewport_height") or 0) != expected[1]:
        findings.append(GateFinding("render_probe_viewport_invalid", f"{label} browser probe has the wrong viewport"))
    if label == "desktop" and int(probe.get("h1_line_count") or 0) > 2:
        findings.append(
            GateFinding(
                "hero_heading_too_many_lines",
                "desktop hero heading exceeds two rendered lines",
                evidence={"line_count": int(probe.get("h1_line_count") or 0)},
            )
        )
    subtext = _normalize_text(str(probe.get("hero_subtext") or ""))
    word_count = len(re.findall(r"\b[\w'-]+\b", subtext))
    if word_count > 20:
        findings.append(
            GateFinding(
                "hero_subtext_too_long",
                "hero support copy exceeds twenty words",
                evidence={"word_count": word_count, "text": subtext},
            )
        )
    if int(probe.get("hero_subtext_line_count") or 0) > 4:
        findings.append(
            GateFinding(
                "hero_subtext_too_many_lines",
                "hero support copy exceeds four rendered lines",
                evidence={"line_count": int(probe.get("hero_subtext_line_count") or 0)},
            )
        )
    teasers = list(probe.get("hero_price_teasers_after_cta") or ())
    if teasers:
        findings.append(
            GateFinding(
                "hero_price_teaser_after_cta",
                "pricing teaser appears below the hero CTA group",
                evidence={"texts": teasers},
            )
        )
    if int(probe.get("decorative_dot_count") or 0) > 0:
        findings.append(
            GateFinding(
                "decorative_dots",
                "non-semantic decorative status dots are forbidden",
                evidence={"count": int(probe.get("decorative_dot_count") or 0)},
            )
        )
    section_count = max(1, int(probe.get("section_count") or 0))
    eyebrow_count = int(probe.get("eyebrow_count") or 0)
    eyebrow_limit = math.ceil(section_count / 3)
    if eyebrow_count > eyebrow_limit:
        findings.append(
            GateFinding(
                "excessive_eyebrows",
                "uppercase tracked eyebrow labels exceed the Taste limit",
                evidence={"count": eyebrow_count, "limit": eyebrow_limit, "labels": probe.get("eyebrow_labels") or []},
            )
        )
    if int(probe.get("generic_equal_step_group_count") or 0) > 0:
        findings.append(
            GateFinding("generic_equal_step_cards", "equal cards with generic Step/Stage/Phase labels are forbidden")
        )
    body_text = str(probe.get("body_text") or "")
    if "—" in body_text or "–" in body_text:
        findings.append(GateFinding("visible_dash_forbidden", "visible em/en dashes fail the Taste preflight"))
    if int(probe.get("header_count") or 0) != 1 or int(probe.get("main_count") or 0) != 1 or int(probe.get("nested_main_count") or 0):
        findings.append(
            GateFinding(
                "wrapper_conflict",
                "landing must have one visible header, one visible main, and no nested main wrapper",
                evidence={
                    "headers": int(probe.get("header_count") or 0),
                    "mains": int(probe.get("main_count") or 0),
                    "nested_mains": int(probe.get("nested_main_count") or 0),
                },
            )
        )
    if int(probe.get("scroll_width") or 0) > int(probe.get("document_width") or expected[0]) + 1:
        findings.append(GateFinding("horizontal_overflow", f"{label} render overflows horizontally"))
    header_width = float(probe.get("header_width") or 0)
    if header_width and abs(header_width - expected[0]) > 2:
        findings.append(
            GateFinding(
                "header_not_page_spanning",
                "the canonical public header does not span the viewport",
                evidence={"header_width": header_width, "viewport_width": expected[0]},
            )
        )
    if label == "desktop":
        wrapped_ctas = [
            str(entry.get("label") or "")
            for entry in probe.get("ctas") or ()
            if isinstance(entry, Mapping) and int(entry.get("line_count") or 0) > 1
        ]
        if wrapped_ctas:
            findings.append(
                GateFinding(
                    "cta_button_wrapped",
                    "desktop CTA labels must render on one line",
                    evidence={"labels": wrapped_ctas},
                )
            )
        if int(probe.get("navigation_line_count") or 0) > 1 or float(probe.get("navigation_height") or 0) > 80:
            findings.append(
                GateFinding(
                    "navigation_geometry_invalid",
                    "desktop navigation must use one line and be at most 80px tall",
                    evidence={
                        "lines": int(probe.get("navigation_line_count") or 0),
                        "height": float(probe.get("navigation_height") or 0),
                    },
                )
            )
        theme_modes = {str(value) for value in probe.get("theme_modes") or ()}
        if len(theme_modes) > 1 and not re.search(r"(?i)\b(?:theme switch|color block story)\b", design):
            findings.append(
                GateFinding(
                    "theme_lock_broken",
                    "rendered sections switch between light and dark themes",
                    evidence={"modes": sorted(theme_modes)},
                )
            )
        accent_colors = [str(value) for value in probe.get("accent_colors") or ()]
        if _hue_cluster_count(accent_colors) > 1:
            findings.append(
                GateFinding(
                    "color_lock_broken",
                    "rendered controls use more than one accent-color family",
                    evidence={"colors": accent_colors},
                )
            )
        shape_radii = probe.get("shape_radii") if isinstance(probe.get("shape_radii"), Mapping) else {}
        inconsistent_roles = {
            str(role): sorted({int(value) for value in values})
            for role, values in shape_radii.items()
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) and len(set(values)) > 1
        }
        if inconsistent_roles and not re.search(r"(?i)\b(?:radius|corner)\s+(?:rule|system)\b", design):
            findings.append(
                GateFinding(
                    "shape_lock_broken",
                    "rendered components use inconsistent corner radii without a documented rule",
                    evidence={"roles": inconsistent_roles},
                )
            )
    if not bool(probe.get("hero_complete")) or not bool(probe.get("primary_cta_visible")):
        findings.append(GateFinding("hero_first_viewport_incomplete", f"{label} header/hero/primary CTA is incomplete"))
    if bool(probe.get("next_section_intrusion")) and not re.search(
        r"(?i)\b(?:deliberate|intentional)\s+(?:continuation|peek|next[- ]section)\b", design
    ):
        findings.append(GateFinding("accidental_next_section_intrusion", f"{label} first viewport leaks the next section"))
    layout_contracts: set[str] = set()
    for contract_line in design.splitlines():
        if not re.search(r"(?i)\b(?:hero|composition|layout)\b", contract_line):
            continue
        names_desktop = bool(re.search(r"(?i)\bdesktop\b", contract_line))
        names_mobile = bool(re.search(r"(?i)\bmobile\b", contract_line))
        if label == "desktop" and names_mobile and not names_desktop:
            continue
        if label == "mobile" and names_desktop and not names_mobile:
            continue
        if re.search(r"(?i)\b(?:split|two[- ]column|side[- ]by[- ]side)\b", contract_line):
            layout_contracts.add("split")
        if re.search(r"(?i)\b(?:cent(?:er|re)(?:ed)?|single[- ]column centered)\b", contract_line):
            layout_contracts.add("centered")
        if re.search(r"(?i)\b(?:stacked|single[- ]column)\b", contract_line):
            layout_contracts.add("stacked")
    actual_layout = str(probe.get("hero_layout") or "")
    if len(layout_contracts) == 1 and actual_layout and actual_layout not in layout_contracts:
        findings.append(
            GateFinding(
                "design_render_contradiction",
                f"{label} hero layout contradicts DESIGN.md",
                "DESIGN.md",
                {"expected": next(iter(layout_contracts)), "actual": actual_layout},
            )
        )
    return findings


_CTA_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("signup", re.compile(r"\b(?:sign\s*up|register|join|start|get\s+started|try)\b", re.I)),
    ("login", re.compile(r"\b(?:log\s*in|sign\s*in)\b", re.I)),
    ("contact", re.compile(r"\b(?:contact|talk|reach\s*out|start\s+a\s+project)\b", re.I)),
    ("purchase", re.compile(r"\b(?:buy|subscribe|checkout|purchase)\b", re.I)),
)


def _cta_label_variant(value: str) -> str:
    text = re.sub(r"[$€£]\s*\d+(?:[.,]\d+)?(?:\s*\/\s*\w+)?", "", value.lower())
    text = re.sub(r"\b(?:free|now|today|monthly|annually|per month|per year)\b", "", text)
    return _normalize_text(re.sub(r"[^a-z0-9]+", " ", text))


def _cta_findings(*inspections: RenderInspection) -> list[GateFinding]:
    labels: list[str] = []
    for inspection in inspections:
        for entry in inspection.probe.get("ctas") or ():
            label = str(entry.get("label") if isinstance(entry, Mapping) else entry).strip()
            if label and label not in labels:
                labels.append(label)
    variants: dict[str, set[str]] = defaultdict(set)
    raw: dict[str, list[str]] = defaultdict(list)
    for label in labels:
        for intent, pattern in _CTA_INTENTS:
            if pattern.search(label):
                variants[intent].add(_cta_label_variant(label))
                raw[intent].append(label)
                break
    findings: list[GateFinding] = []
    for intent, values in variants.items():
        if len(values) > 1:
            findings.append(
                GateFinding(
                    "duplicate_cta_intent",
                    f"one CTA intent uses competing labels: {intent}",
                    evidence={"intent": intent, "labels": raw[intent]},
                )
            )
    return findings


def _rendered_source_path(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return parsed._replace(fragment="").geturl()
    return f"{parsed.path}{'?' + parsed.query if parsed.query else ''}"


def _section_contract_findings(
    design: str,
    landing: str,
    desktop: RenderInspection,
    mobile: RenderInspection,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    entries = [entry for entry in desktop.probe.get("section_layouts") or () if isinstance(entry, Mapping)]
    if not entries:
        return [GateFinding("section_inspection_missing", "desktop render has no per-section inspection facts")]
    families = [str(entry.get("family") or "") for entry in entries]
    distinct = {family for family in families if family}
    minimum_families = min(len(entries), 4)
    if len(distinct) < minimum_families:
        findings.append(
            GateFinding(
                "section_layout_diversity_insufficient",
                "rendered sections do not meet Taste's contextual layout-family floor",
                evidence={"families": families, "minimum": minimum_families},
            )
        )
    repeated_families = sorted(family for family in distinct if families.count(family) > 1)
    if repeated_families:
        findings.append(
            GateFinding(
                "section_layout_family_reused",
                "each rendered section layout family may appear at most once",
                evidence={"families": families, "reused": repeated_families},
            )
        )
    for index in range(max(0, len(families) - 2)):
        run = families[index : index + 3]
        if run[0] and len(set(run)) == 1:
            findings.append(
                GateFinding(
                    "section_layout_repeated_consecutively",
                    "three consecutive sections use the same rendered layout family",
                    evidence={"start": index + 1, "family": run[0]},
                )
            )
    image_plan = _parse_image_plan(design)
    if not image_plan:
        findings.append(GateFinding("image_plan_missing", "DESIGN.md needs a per-section Image Plan", "DESIGN.md"))
    image_sections: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        raw_key = str(entry.get("key") or "")
        key = _normalize_section_key(raw_key)
        actual = tuple(
            dict.fromkeys(_rendered_source_path(str(source)) for source in entry.get("image_srcs") or ())
        )
        if key not in image_plan:
            findings.append(
                GateFinding(
                    "image_plan_section_missing",
                    "rendered section is absent from DESIGN.md Image Plan",
                    "DESIGN.md",
                    {"section": raw_key},
                )
            )
        else:
            declared = tuple(_rendered_source_path(source) for source in image_plan[key])
            if set(actual) != set(declared):
                findings.append(
                    GateFinding(
                        "image_plan_render_contradiction",
                        "rendered section images contradict DESIGN.md Image Plan",
                        "DESIGN.md",
                        {"section": raw_key, "declared": list(declared), "rendered": list(actual)},
                    )
                )
        for source in actual:
            image_sections[source].append(raw_key)
    for source, section_keys in image_sections.items():
        if len(set(section_keys)) > 1:
            findings.append(
                GateFinding(
                    "image_crop_reused",
                    "the same image crop is reused across multiple sections",
                    source,
                    {"sections": section_keys},
                )
            )
    mobile_entries = [entry for entry in mobile.probe.get("section_layouts") or () if isinstance(entry, Mapping)]
    desktop_keys = [_normalize_section_key(str(entry.get("key") or "")) for entry in entries]
    mobile_keys = [_normalize_section_key(str(entry.get("key") or "")) for entry in mobile_entries]
    if desktop_keys != mobile_keys:
        findings.append(
            GateFinding(
                "responsive_section_contract_changed",
                "desktop and mobile renders expose different section identities or order",
                evidence={"desktop": desktop_keys, "mobile": mobile_keys},
            )
        )
    rendered_sources = list(image_sections)
    uses_unsplash = "unsplash.com" in landing.lower() or any(
        "unsplash.com" in source.lower() for source in rendered_sources
    )
    unsplash_explicitly_allowed = re.search(
        r"(?i)\bunsplash\b.{0,80}\b(?:explicitly\s+)?(?:allowed|approved|provided)\b",
        design,
    ) is not None
    if uses_unsplash and not unsplash_explicitly_allowed:
        findings.append(
            GateFinding(
                "unsplash_asset_not_approved",
                "Taste permits Unsplash only when the brief explicitly allows it",
            )
        )
    return findings


def _static_source_findings(landing: str, design: str, tokens_source: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    if "—" in landing or "–" in landing:
        findings.append(GateFinding("source_dash_forbidden", "landing source contains em/en dashes", "src/screens/landing.tsx"))
    if re.search(r"(?i)\b(?:Step|Stage|Phase|Pass)\s*\{", landing) and re.search(r"(?:md|lg):grid-cols-3", landing):
        findings.append(
            GateFinding(
                "source_generic_equal_step_cards",
                "landing source encodes a three-equal-card generic step pattern",
                "src/screens/landing.tsx",
            )
        )
    if re.search(r"(?:h|w|size)-\[?\d+(?:px)?\]?[^\n\"']*rounded-full", landing) and "aria-hidden" in landing:
        findings.append(
            GateFinding(
                "source_decorative_dot_risk",
                "landing source contains a small aria-hidden circular decoration",
                "src/screens/landing.tsx",
            )
        )
    if re.search(r"window\.addEventListener\(\s*['\"]scroll['\"]", landing):
        findings.append(
            GateFinding(
                "source_scroll_listener_forbidden",
                "landing source attaches a raw window scroll listener",
                "src/screens/landing.tsx",
            )
        )
    if re.search(r"(?<!min-)\bh-screen\b", landing):
        findings.append(
            GateFinding(
                "source_viewport_unstable",
                "landing source uses h-screen instead of a stable dynamic viewport minimum",
                "src/screens/landing.tsx",
            )
        )
    if re.search(r"\bpt-(?:28|32|36|40|44|48|52|56|60|64|72|80|96)\b", landing):
        findings.append(
            GateFinding(
                "source_hero_padding_risk",
                "landing source contains desktop top padding above the Taste pt-24 cap",
                "src/screens/landing.tsx",
            )
        )
    design_read, foundation, dials = _parse_design(design)
    if not design_read:
        findings.append(GateFinding("design_read_missing", "DESIGN.md has no Design Read", "DESIGN.md"))
    if not foundation:
        findings.append(GateFinding("design_foundation_missing", "DESIGN.md has no selected foundation", "DESIGN.md"))
    for name in ("DESIGN_VARIANCE", "MOTION_INTENSITY", "VISUAL_DENSITY"):
        value = dials.get(name, 0)
        if not 1 <= value <= 10:
            findings.append(GateFinding("design_dial_invalid", f"{name} must be an integer from 1 to 10", "DESIGN.md"))
    tokens = _parse_tokens(tokens_source)
    for name, declared in re.findall(r"`?(--[a-zA-Z0-9_-]+)`?\s*:\s*([^\s,)]+)", design):
        actual = tokens.get(name)
        if actual is not None and actual.lower() != declared.rstrip(".;").lower():
            findings.append(
                GateFinding(
                    "design_token_contradiction",
                    f"DESIGN.md declares {name}={declared}, source uses {actual}",
                    "src/tokens.css",
                )
            )
    motion = dials.get("MOTION_INTENSITY", 0)
    source_has_motion = bool(re.search(r"\b(?:motion\.|whileInView|animate-[a-z]|@keyframes)\b", landing))
    if motion > 4 and not source_has_motion:
        findings.append(
            GateFinding(
                "design_motion_contradiction",
                "DESIGN.md claims a motion band above 4 but the landing has no motion implementation",
                "src/screens/landing.tsx",
            )
        )
    return findings


_FAKE_UI_PROMPT_PATTERNS = (
    re.compile(r"(?is)\b(?:tablet|phone|monitor|screen|laptop)\b.{0,100}\b(?:display|show|render)\w*\b.{0,140}\b(?:report|dashboard|form|document|interface|ui|notes?|text)\b"),
    re.compile(r"(?is)\b(?:handwritten|rough)\s+(?:text|notes?)\b.{0,120}\b(?:transform|turn|become)\w*\b.{0,120}\b(?:digital|structured|clean)\s+(?:report|document|ui)\b"),
)


def _asset_findings(
    *,
    landing: str,
    design: str,
    assets: Sequence[Mapping[str, Any]],
    inspections: Mapping[str, AssetVisualInspection],
    renders: Sequence[RenderInspection],
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    rendered_paths = {
        urlparse(str(src)).path
        for render in renders
        for src in render.probe.get("image_srcs") or ()
    }
    for asset in assets:
        public_path = str(asset["public_path"])
        receipt = asset["receipt"]
        if public_path not in landing:
            findings.append(GateFinding("asset_unused_in_source", "generated asset is not used by landing source", public_path))
        if public_path not in rendered_paths:
            findings.append(GateFinding("asset_unused_in_render", "generated asset is absent from inspected renders", public_path))
        if public_path not in design:
            findings.append(GateFinding("asset_missing_from_design", "generated asset is absent from DESIGN.md", public_path))
        prompt = _normalize_text(str(receipt.get("prompt") or ""))
        prompt_lower = prompt.lower()
        required_negative_terms = ("text", "ui", "logos", "watermarks", "browser chrome", "fake product controls")
        if not all(term in prompt_lower for term in required_negative_terms):
            findings.append(
                GateFinding(
                    "asset_prompt_safety_incomplete",
                    "asset prompt omits part of Taste's no-text/no-fake-UI contract",
                    str(asset["receipt_path"]),
                )
            )
        if any(pattern.search(prompt) for pattern in _FAKE_UI_PROMPT_PATTERNS):
            findings.append(
                GateFinding(
                    "asset_prompt_fake_ui_risk",
                    "asset prompt positively requests a text-bearing or fabricated product UI surface",
                    str(asset["receipt_path"]),
                )
            )
        inspection = inspections.get(public_path)
        if inspection is None or not inspection.inspected:
            findings.append(
                GateFinding(
                    "asset_visual_inspection_missing",
                    "generated asset lacks independent full-resolution visual inspection",
                    public_path,
                )
            )
            continue
        if inspection.image_sha256 != asset["sha256"]:
            findings.append(GateFinding("asset_visual_inspection_stale", "asset inspection digest is stale", public_path))
        if (inspection.inspected_width, inspection.inspected_height) != (asset["width"], asset["height"]):
            findings.append(
                GateFinding(
                    "asset_visual_inspection_not_full_resolution",
                    "asset inspection dimensions do not match the generated PNG",
                    public_path,
                    {
                        "inspected": [inspection.inspected_width, inspection.inspected_height],
                        "actual": [asset["width"], asset["height"]],
                    },
                )
            )
        if not inspection.source:
            findings.append(
                GateFinding(
                    "asset_visual_inspection_source_missing",
                    "asset inspection lacks a key-free inspector source receipt",
                    public_path,
                )
            )
        if inspection.detected_text:
            findings.append(
                GateFinding(
                    "asset_baked_text_detected",
                    "generated asset contains baked-in text or text-like artifacts",
                    public_path,
                    {"detected_text": list(inspection.detected_text)},
                )
            )
        if inspection.fake_ui_detected:
            findings.append(
                GateFinding(
                    "asset_fake_ui_detected",
                    "generated asset contains fabricated UI/product controls",
                    public_path,
                    {"artifacts": list(inspection.artifact_labels)},
                )
            )
    return findings


def _snapshot_findings(current: TasteDesignSnapshot, baseline: TasteDesignSnapshot | None) -> list[GateFinding]:
    if baseline is None:
        return []
    findings: list[GateFinding] = []
    for name in ("design_read_sha256", "foundation_sha256", "design_sha256", "tokens_sha256", "landing_sha256"):
        if getattr(current, name) != getattr(baseline, name):
            findings.append(
                GateFinding(
                    "cross_worker_design_dilution",
                    f"later product work changed the pinned Taste {name.removesuffix('_sha256').replace('_', ' ')}",
                    DESIGN_SNAPSHOT_RELATIVE_PATH,
                    {"field": name},
                )
            )
    if dict(current.dials) != dict(baseline.dials):
        findings.append(GateFinding("cross_worker_design_dilution", "later product work changed Taste dial values"))
    if dict(current.tokens) != dict(baseline.tokens):
        findings.append(GateFinding("cross_worker_design_dilution", "later product work changed the pinned token language"))
    if dict(current.assets) != dict(baseline.assets):
        findings.append(GateFinding("cross_worker_design_dilution", "later product work changed pinned landing assets"))
    return findings


def validate_taste_publication(
    workspace_path: str | Path,
    *,
    desktop: RenderInspection | Mapping[str, Any],
    mobile: RenderInspection | Mapping[str, Any],
    asset_inspections: Mapping[str, AssetVisualInspection | Mapping[str, Any]],
    preflight_evidence: Mapping[str, PreflightEvidenceItem | Mapping[str, Any]] | None = None,
    baseline_snapshot: TasteDesignSnapshot | Mapping[str, Any] | None = None,
) -> TasteGateResult:
    """Validate a rendered Taste landing before publication.

    ``asset_inspections`` must contain independently inspected, digest-bound
    results for every generated asset.  This function performs no provider call
    and reads no credential.  Production should populate those results through
    :func:`run_asset_visual_inspections` with a Safebox-authorized inspector.
    """

    root = Path(workspace_path).resolve()
    desktop_value = desktop if isinstance(desktop, RenderInspection) else RenderInspection.from_mapping(desktop)
    mobile_value = mobile if isinstance(mobile, RenderInspection) else RenderInspection.from_mapping(mobile)
    normalized_inspections = {
        str(path): value if isinstance(value, AssetVisualInspection) else AssetVisualInspection.from_mapping(value)
        for path, value in asset_inspections.items()
    }
    findings: list[GateFinding] = []
    snapshot: TasteDesignSnapshot | None = None
    try:
        design = _bounded_text(root / "DESIGN.md")
        landing = _bounded_text(root / "src" / "screens" / "landing.tsx")
        tokens_source = _bounded_text(root / "src" / "tokens.css")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return TasteGateResult((GateFinding("taste_source_invalid", str(exc)),), None)
    findings.extend(_preflight_findings(preflight_evidence or {}))
    findings.extend(_static_source_findings(landing, design, tokens_source))
    findings.extend(_render_findings(desktop_value, expected=DESKTOP_VIEWPORT, label="desktop", design=design))
    findings.extend(_render_findings(mobile_value, expected=MOBILE_VIEWPORT, label="mobile", design=design))
    findings.extend(_cta_findings(desktop_value, mobile_value))
    findings.extend(_section_contract_findings(design, landing, desktop_value, mobile_value))
    assets, asset_receipt_findings = _asset_receipts(root)
    findings.extend(asset_receipt_findings)
    findings.extend(
        _asset_findings(
            landing=landing,
            design=design,
            assets=assets,
            inspections=normalized_inspections,
            renders=(desktop_value, mobile_value),
        )
    )
    try:
        snapshot = capture_design_snapshot(root)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        findings.append(GateFinding("design_snapshot_invalid", str(exc), DESIGN_SNAPSHOT_RELATIVE_PATH))
    baseline: TasteDesignSnapshot | None
    if isinstance(baseline_snapshot, TasteDesignSnapshot):
        baseline = baseline_snapshot
    elif isinstance(baseline_snapshot, Mapping):
        baseline = TasteDesignSnapshot.from_mapping(baseline_snapshot)
    else:
        try:
            baseline = load_design_snapshot(root)
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            findings.append(GateFinding("design_snapshot_unreadable", str(exc), DESIGN_SNAPSHOT_RELATIVE_PATH))
            baseline = None
    if snapshot is not None:
        findings.extend(_snapshot_findings(snapshot, baseline))
    deduplicated: list[GateFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (finding.code, finding.message, finding.path)
        if key not in seen:
            seen.add(key)
            deduplicated.append(finding)
    return TasteGateResult(tuple(deduplicated), snapshot)


__all__ = [
    "AssetVisualInspection",
    "CANONICAL_PREFLIGHT_IDS",
    "DESIGN_SNAPSHOT_RELATIVE_PATH",
    "DESKTOP_VIEWPORT",
    "GateFinding",
    "MOBILE_VIEWPORT",
    "PreflightEvidenceItem",
    "RenderInspection",
    "SafeboxAssetInspector",
    "TASTE_RENDER_INSPECTION_JS",
    "TasteDesignSnapshot",
    "TasteGateResult",
    "capture_design_snapshot",
    "load_design_snapshot",
    "run_asset_visual_inspections",
    "validate_taste_publication",
    "write_design_snapshot",
]
