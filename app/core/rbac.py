"""Module-level access roles (Hierarchical model).

Single source of truth for which roles may access each restricted module.
Mirror of the frontend `lib/rbac.ts`.

    Module        CEO  CFO  Mgr  Exec
    Agents         ✓    ✓    ✓    –
    Services       ✓    ✓    ✓    –   (edit; every role lists active services)
    Projects       ✓    ✓    ✓    –   (edit and send work orders; every role views)
    Taxation       ✓    ✓    ✓    –
    Approvals      ✓    ✓    ✓    –
    Audit Trail    ✓    –    –    –
    (everything else: all four authenticated roles)
"""

from app.models import UserRole

# Everyone except the Finance Executive.
NON_EXEC = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)

# Leadership only.
CEO_ONLY = (UserRole.ADMIN_CEO,)
