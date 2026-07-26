"""Generate the local-only preview from the canonical verification-code email."""

from __future__ import annotations

from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
CANONICAL_PATH = DIRECTORY / "verify_email_by_code.html"
PREVIEW_PATH = DIRECTORY / "verify_email_by_code.preview.html"
LIQUID_CODE_PLACEHOLDER = "{{ code }}"
SAMPLE_CODE = "547293"


def render_preview(canonical: str, sample_code: str = SAMPLE_CODE) -> str:
    """Replace only the single approved Liquid placeholder with sample data."""
    if canonical.count(LIQUID_CODE_PLACEHOLDER) != 1:
        raise ValueError("canonical template must contain one code placeholder")
    if not (sample_code.isascii() and sample_code.isdigit() and len(sample_code) == 6):
        raise ValueError("preview code must be six ASCII digits")
    return canonical.replace(LIQUID_CODE_PLACEHOLDER, sample_code, 1)


def main() -> None:
    canonical = CANONICAL_PATH.read_text(encoding="utf-8")
    PREVIEW_PATH.write_text(render_preview(canonical), encoding="utf-8")


if __name__ == "__main__":
    main()
