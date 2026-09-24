"""Interpretation switches for preregistered LB probes. The defaults are the submitted C2 behaviour; a probe package
differs from C2 only in this file."""

# v10·v11·v13 judged on software services (scope family sw) as on other competition services.
SW_SERVICE_COMPETITIVE = True
# v20 judged only at or above this 추정가격 (0 = every amount, 지침 제3조②).
V20_MIN_ESTIMATE = 0
# v11 judged on 수의계약 too (판로지원법 제7조① names 입찰; default excludes 수의계약).
V11_PRIVATE = False
# v14–v18 judged on 수의계약 too. False since LB probe P3a (2026-09-25: P3a − C2 = +0.0039; the organizer does not mark them there).
SIZE_PRIVATE = False
# "동등 이상" leaves a designation a v9 violation (approved literal default).
V9_EQUIVALENT_VIOLATION = True
