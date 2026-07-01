from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_SOURCE = REPO_ROOT / "web" / "src" / "litebulb" / "product" / "Product.tsx"
PRODUCT_CSS = REPO_ROOT / "web" / "src" / "litebulb" / "product" / "product.css"
CEO_PROMPT = REPO_ROOT / "plugins" / "takyon" / "prompts" / "ceo.md"


def test_litebulb_chat_renders_finished_agent_messages_as_markdown():
    source = PRODUCT_SOURCE.read_text(encoding="utf-8")

    assert 'import ReactMarkdown from "react-markdown";' in source
    assert 'import remarkBreaks from "remark-breaks";' in source
    assert 'import remarkGfm from "remark-gfm";' in source
    assert "function AgentMessageMarkdown({" in source
    assert "businessSlug = \"\"" in source
    assert "knownArtifacts = []" in source
    assert '<div className="lb-msg__md">' in source
    assert 'remarkPlugins={[remarkGfm, remarkBreaks]}' in source
    assert "const linked = linkifyArtifactMentions(safe, businessSlug, knownArtifacts);" in source
    assert 'businessSlug={business.slug}' in source
    assert 'knownArtifacts={artifactPaths}' in source
    assert '{message.working && (' in source
    assert '<span className="lb-msg__work">' in source


def test_litebulb_chat_styles_markdown_spacing_for_agent_messages():
    source = PRODUCT_CSS.read_text(encoding="utf-8")

    assert ".lb-msg__md { display: flex; flex-direction: column; gap: 0.75em; }" in source
    assert ".lb-msg__md p," in source
    assert "white-space: pre-wrap;" in source
    assert ".lb-msg__md hr {" in source
    assert ".lb-msg__md table {" in source


def test_ceo_prompt_asks_for_normal_user_facing_operator_chat():
    source = CEO_PROMPT.read_text(encoding="utf-8")

    assert "User-facing operator replies should read like normal chat" in source
    assert "Use readable Markdown with real paragraph breaks" in source
    assert "Avoid meta-openers such as `good, now I'll`" in source


def test_litebulb_chat_linkifies_unique_artifact_basenames():
    source = PRODUCT_SOURCE.read_text(encoding="utf-8")

    assert "function linkifyArtifactMentions(" in source
    assert "const basenameCounts = new Map<string, number>();" in source
    assert "if (base && basenameCounts.get(base) === 1) {" in source
    assert "replacements.set(base, normalized);" in source
    assert "buildTakyonBusinessArtifactUrl(slug, target)" in source
