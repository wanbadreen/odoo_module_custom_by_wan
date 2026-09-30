"""Allow eligible packages with no included card in the new packing phase."""


def migrate(cr, version):
    cr.execute(
        "ALTER TABLE motogene_scratch_package "
        "DROP CONSTRAINT IF EXISTS motogene_scratch_package_scratch_package_cards_positive"
    )
