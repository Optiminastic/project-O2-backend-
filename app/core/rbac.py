"""Module-level access roles (Hierarchical model).

Single source of truth for which roles may access each restricted module.
Mirror of the frontend `lib/rbac.ts`.

    Module        CEO  CFO  Mgr  Exec  Coord
    Agents         ✓    ✓    ✓    –     –
    Services       ✓    ✓    ✓    –     –   (edit; every role lists active services)
    Projects       ✓    ✓    ✓    –     –   (edit and send work orders; every role views)
    Banks          ✓    ✓    ✓    –     –   (add and edit; every role lists active banks)
    Taxation       ✓    ✓    ✓    –     –
    Approvals      ✓    ✓    ✓    –     –
    Audit Trail    ✓    –    –    –     –
    PI approval    ✓    ✓    –    –     ✓   (approve a proforma, which sends it to the client)
    (everything else: every authenticated role; the Coordinator only reads)
"""

from app.models import UserRole

# Everyone except the Finance Executive.
NON_EXEC = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.FINANCE_MANAGER)

# Leadership only.
CEO_ONLY = (UserRole.ADMIN_CEO,)

# Approving a proforma sends it to the client for confirmation (the coordinator's job).
PI_APPROVER_ROLES = (UserRole.ADMIN_CEO, UserRole.CFO, UserRole.COORDINATOR)
