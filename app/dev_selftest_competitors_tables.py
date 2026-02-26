from __future__ import annotations

from app.main import (
    _build_competitors_compare_and_site_text,
    _format_block_display,
    _validate_sheet1_matrix_rows,
    _validate_sheet2_site_columns_rows,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    # 1) formatter: label == id -> label only.
    out_same = _format_block_display("Hero-оффер", "Hero-оффер", "fallback")
    _assert(out_same == "Hero-оффер", f"expected no duplication, got: {out_same}")

    # 2) formatter: label != id -> label (id).
    out_diff = _format_block_display("Hero-оффер", "hero_main", "fallback")
    _assert(out_diff == "Hero-оффер (hero_main)", f"unexpected mixed format: {out_diff}")

    # 3) formatter: empty label/id -> fallback.
    out_fallback = _format_block_display("", "", "fallback_block")
    _assert(out_fallback == "fallback_block", f"fallback failed: {out_fallback}")

    pages = [
        {"url": "https://www.it-agency.ru"},
        {"url": "https://www.rush-agency.ru"},
        {"url": "https://www.ashmanov.com"},
    ]
    rows = [
        {
            "site": "it-agency.ru",
            "page_url": "https://www.it-agency.ru",
            "l2_id": "hero_main",
            "l2_label_ru": "Hero-оффер",
            "block_name": "hero_main",
            "block_index": 1,
            "notes": "Оффер + CTA",
        },
        {
            "site": "rush-agency.ru",
            "page_url": "https://www.rush-agency.ru",
            "l2_id": "hero_main",
            "l2_label_ru": "Hero-оффер",
            "block_name": "hero_main",
            "block_index": 1,
            "notes": "Оффер + форма",
        },
        {
            "site": "ashmanov.com",
            "page_url": "https://www.ashmanov.com",
            "l2_id": "hero_main",
            "l2_label_ru": "Hero-оффер",
            "block_name": "hero_main",
            "block_index": 1,
            "notes": "Оффер + CTA",
        },
        {
            "site": "it-agency.ru",
            "page_url": "https://www.it-agency.ru",
            "l2_id": "cases_list",
            "l2_label_ru": "Кейсы",
            "block_name": "cases_list",
            "block_index": 2,
            "notes": "Карточки кейсов",
        },
    ]
    _, _, matrix_rows, site_columns_rows = _build_competitors_compare_and_site_text(rows, pages)

    # 4) matrix checks.
    ok1, reason1 = _validate_sheet1_matrix_rows(matrix_rows)
    _assert(ok1, f"matrix validation failed: {reason1}")
    _assert(matrix_rows[0][0] == "Блоки / Сайты", "matrix header mismatch")
    _assert(all(" (" not in r[0] or ")" in r[0] for r in matrix_rows[1:]), "unexpected label format")
    _assert("Hero-оффер (Hero-оффер)" not in "\n".join(row[0] for row in matrix_rows[1:]), "duplicate label/id still present")

    # 5) site columns checks.
    ok2, reason2 = _validate_sheet2_site_columns_rows(site_columns_rows)
    _assert(ok2, f"site columns validation failed: {reason2}")
    _assert(len(site_columns_rows[0]) == 3, "expected 3 site columns")

    print("OK: competitors tables self-test passed")


if __name__ == "__main__":
    run()
