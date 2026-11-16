# Failure modes, consistency and security

This document describes **recommended engineering behavior**, not a guarantee that the private deployment implements every mitigation.

| Scenario | Risk | Recommended behavior |
|---|---|---|
| Request has a malformed Steam64 ID | Wrong person or failed lookup | Reject invalid syntax before calling remote services |
| Steam64 is syntactically valid but unregistered | Unauthorized grant | Confirm eligibility with the identity service |
| Same request submitted repeatedly | Duplicate review load | Persist deduplication key and cooldown policy |
| Two staff members approve concurrently | Repeated entitlement grants | Atomic claim of request state before calling API |
| API times out after possibly succeeding | Blind retry can duplicate effect | Mark outcome uncertain and reconcile status |
| API definitively rejects delivery | Misleading success response | Persist failure; explain next staff action |
| API returns success but local write fails | Lost audit trail | Reconciliation queue / outbox approach |
| Entitlement expires | Access drift | Verify expiry and renewal policy in scheduled job |
| Discord user clicks privileged action | Improper access | Enforce permissions on the backend, not only via UI |

## Testing plan

A separately released demonstration service should include:
- invalid IDs and missing registration;
- rejected requests and unauthorized reviews;
- duplicate approvals and concurrent requests;
- API timeout, retry and confirmed rejection;
- persistence across restart and expiry behavior;
- audit events and state transitions.

Tests should only use fake IDs and mock integrations. No private API endpoint, credential, or real player record belongs in a portfolio repository.

## Publication boundary

No production implementation is released here. The public `src/public_interface.py` intentionally raises `NotImplementedError`.
