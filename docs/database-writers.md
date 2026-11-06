# Database writer admission

The app and permuter are separate processes. SQLite WAL permits concurrent readers and one
writer. A thread mutex in either process cannot coordinate their writes. Previously a long
restore could exceed another process's SQLite busy timeout, failing evidence or claim cleanup.

`WriterGate` admits one cooperating writer per canonical database path before SQLite BEGIN.
An OS advisory lock on the persistent `.writer.lock` sidecar coordinates processes and threads.
Waiting does not hold `Store.lock` or begin a SQLite transaction. Independent WAL readers keep
working. The sidecar stays on disk to preserve one lock identity across open file handles.
Process exit releases the OS lock; it does not leave a persistent claimed-writer flag.

Store schema initialization, normal and optional transactions, compaction and optimizer writes
use admission. Match-library writers use their database's own gate; library reads bypass it.
Callbacks run after COMMIT and after releasing admission. Failed bodies or COMMIT roll back and
never replay. `try_tx` yields None immediately when another writer owns admission. `tx(wait=...)`
lets maintenance report waits and cancel. Nested transactions on one Store join the outer one;
recursive writes through an independent connection fail promptly to prevent self-deadlock.

App writers wait for admission without spending SQLite's busy timeout. Raw external SQLite
writers do not participate in this protocol and retain SQLite's configured timeout and error
semantics. Genuine body/SQL errors still propagate. All app/worker processes need the new version.
Atomic restores can still queue writes; this coordination preserves their atomicity.

Unix uses `flock`; Windows uses CRT byte-region locks. A region can extend past EOF, so creating
the zero-byte lock file requires no initialization write. See Microsoft's
[_locking documentation](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking).
