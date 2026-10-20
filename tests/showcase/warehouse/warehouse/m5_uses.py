# M5: a private path part directly under the package root, imported from
# another package root entirely -- finding.
import catalog._internal.tax as tax


def use():
    return tax.rate()
