from app.ingestion.normalization import (
    CardSpec,
    canonical_key,
    normalize_name,
    normalize_number,
    rarity_class,
    resolve_ja_to_en,
)


def test_name_and_number_normalization():
    assert normalize_name("Lizardon ex") == normalize_name("Charizard ex") == "charizardex"
    assert normalize_name("Pokémon Umbreon VMAX") == "umbreonvmax"
    assert normalize_number("004/102") == "4" and normalize_number("SV-P 208") == "SV-P208"
    assert rarity_class("SAR", "ja") == "SIR" and rarity_class("AR", "ja") == "IR"


def test_identical_mapping_across_mapped_sets():
    ja = CardSpec("Lizardon ex", "sv2a", "201/165", "SAR", "ja", "2023-06-16")
    en = CardSpec("Charizard ex", "sv3pt5", "199/165", "SIR", "en", "2023-09-22")
    res = resolve_ja_to_en(ja, [en])
    assert res.equivalence == "identical" and res.en_candidate is en
    assert res.canonical_key == canonical_key("Charizard ex", "sv3pt5", "199/165", "SIR")


def test_split_jp_sets_map_to_one_en_set():
    ja = CardSpec("Rayquaza VMAX", "s7r", "080/067", "SA", "ja", "2021-07-09")
    en = CardSpec("Rayquaza VMAX", "swsh7", "218/203", "SA", "en", "2021-08-27")
    assert resolve_ja_to_en(ja, [en]).equivalence == "identical"


def test_promo_exclusive_override_and_fallbacks():
    ja = CardSpec("Pikachu", "SV-P", "208", "Promo", "ja", "2023-10-01")
    en = CardSpec("Pikachu", "svp", "085", "Promo", "en", "2023-09-01")
    res = resolve_ja_to_en(ja, [en])
    assert res.equivalence == "promo_exclusive" and res.en_candidate is en
    illustrator = CardSpec("Pikachu Illustrator", "PROMO", "ILLUSTRATOR", None, "ja")
    assert resolve_ja_to_en(illustrator, []).equivalence == "no_equivalent"
    unknown_promo = CardSpec("Gengar", "S-P", "999", "Promo", "ja", "2022-01-01")
    assert resolve_ja_to_en(unknown_promo, []).equivalence == "no_equivalent"
    reprint = resolve_ja_to_en(CardSpec("Gengar", "s99", "1", "SR", "ja"), [CardSpec("Gengar", "xx1", "9", "Ultra Rare", "en")])
    assert reprint.equivalence == "reprint"
