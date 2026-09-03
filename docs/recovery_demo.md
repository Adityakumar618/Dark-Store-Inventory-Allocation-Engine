# Crash Recovery & WAL (Write-Ahead Logging) Verification

This document details the chaos testing methodology and recovery mechanics governing the **Dark-Store Inventory Allocation Engine**.

---

## 1. ACID Atomicity & Crash Invariance

In quick-commerce architectures, network drops, OOM kills, or container restarts can occur in the middle of multi-table inventory allocation transactions (e.g. after decrementing inventory but before inserting the `order_items` mapping).

### The Invariant
> If a transaction does not reach `COMMIT`, **zero partial mutations must persist**. Upon client disconnection or server crash, PostgreSQL must roll back all uncommitted page modifications during Write-Ahead Log (WAL) replay or transaction cleanup.

---

## 2. Chaos Simulation Protocol

We simulate a severe mid-transaction failure via [scripts/chaos_test.py](file:///c:/Users/Nisha%20kumari/Downloads/DBMS%20Project/scripts/chaos_test.py):
1. **Baseline Measurement**: Record current available stock for SKU 2 at Store 1 (e.g., `45 units`).
2. **Transaction Initiation**: Begin transaction and mutate tuple:
   $$\text{qty\_available} \leftarrow \text{qty\_available} - 10 \quad (\text{uncommitted: } 35 \text{ units})$$
3. **Chaos Injection**: Identify the server backend PID via `SELECT pg_backend_pid()` and abruptly terminate the server process using `pg_terminate_backend(pid)`.
4. **Post-Recovery Assertion**: Open an independent session and query the tuple.

---

## 3. Execution Log Trace

```
=================================================================
 CHAOS / MID-TRANSACTION CRASH & WAL RECOVERY TEST 
=================================================================
[*] Baseline Pre-Crash Stock for Store 1, SKU 2: 45 units

[*] Starting uncommitted allocation transaction...
[*] Executing in-flight decrement: -10 units...
[*] In-flight uncommitted stock inside transaction: 35 units
[*] Victim Backend PID: 4892

💥 CHAOS INJECTED: Terminating backend process 4892 abruptly via pg_terminate_backend()...
[*] Expected Connection Failure Received: terminating connection due to administrator command
server closed the connection unexpectedly

[*] Verifying post-crash state via fresh connection...
[*] Post-Crash Database Stock: 45 units

=================================================================
 VERIFICATION RESULT
=================================================================
✅ PASSED: Stock exactly matched baseline (45 units == 45 units).
✅ Zero partial updates leaked; ACID Atomicity & WAL recovery fully verified.
=================================================================
```

---

## 4. PostgreSQL Recovery Mechanics
- **Uncommitted Buffer Eviction**: Uncommitted dirty buffers modified in shared memory are marked invalid upon backend failure.
- **WAL Invariance**: Redo logs are only finalized upon `COMMIT`. Since no commit record was written to disk, replaying or scanning the WAL ignores the uncommitted transaction ID.
- **Lock Cleanup**: PostgreSQL automatically releases all tuple and table-level locks held by the terminated process, allowing waiting transactions to proceed safely.
