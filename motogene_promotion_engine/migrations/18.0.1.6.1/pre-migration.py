"""Allow eligible packages with no included card in the new packing phase."""


def migrate(cr, version):
    # Rebuilds may upgrade directly from a version before this model existed.
    cr.execute("SELECT to_regclass(%s)", ["motogene_scratch_package"])
    if not cr.fetchone()[0]:
        return
    cr.execute(
        "ALTER TABLE motogene_scratch_package "
        "DROP CONSTRAINT IF EXISTS motogene_scratch_package_scratch_package_cards_positive"
    )
