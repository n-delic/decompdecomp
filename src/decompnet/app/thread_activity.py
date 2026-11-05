"""OS counters for this process's Python threads, without sampling stacks or suspending work."""
import ctypes
import os
import sys
import threading
import time
from pathlib import Path


class _MacThreadInfo(ctypes.Structure):
    # macOS SDK sys/proc_info.h: proc_threadinfo / PROC_PIDTHREADID64INFO (15).
    _fields_ = [('user',ctypes.c_uint64), ('system',ctypes.c_uint64),
                ('scheduling',ctypes.c_int32*8), ('name',ctypes.c_char*64)]


def cpu_reader():
    if sys.platform == 'darwin':
        try:
            query = ctypes.CDLL('/usr/lib/libproc.dylib').proc_pidinfo
            query.argtypes = [ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
            query.restype = ctypes.c_int
        except (OSError, AttributeError):
            return lambda tid:None
        def read(tid):
            info = _MacThreadInfo()
            if query(os.getpid(),15,tid,ctypes.byref(info),ctypes.sizeof(info)) != ctypes.sizeof(info):
                return None  # A thread can exit between enumerate() and the kernel query.
            return (info.user+info.system)/1_000_000_000
        return read
    if sys.platform.startswith('linux'):
        ticks = os.sysconf('SC_CLK_TCK')
        def read(tid):
            try:
                # comm may contain spaces and parentheses; the fields after its final ')' are fixed.
                fields = Path(f'/proc/self/task/{tid}/stat').read_text().rsplit(')',1)[1].split()
                return (int(fields[11])+int(fields[12]))/ticks
            except (OSError, ValueError, IndexError):
                return None
        return read
    return lambda tid:None


def purpose(name):
    exact = {
        'MainThread':('Server','Runs the local HTTP/MCP event loop and coordinates app requests.'),
        'AnyIO worker thread':('Requests','Runs synchronous request handlers off the server event loop; may be idle.'),
        'autosave':('Saved progress','Checks saved project files and synchronizes verified progress.'),
        'compiler-process-tracker':('Compilers','Tracks compiler subprocesses and their resource budgets.'),
        'detect-agents':('Agent discovery','Finds installed agent apps and their available models.'),
        'sdk-names':('SDK indexing','Identifies project symbols using local SDK signatures.'),
        'focused-agent-deadline':('Agents','Enforces focused-agent time limits.'),
        'resume-work':('Startup','Restores background work after startup.'),
        'startup-maintenance':('Startup maintenance','Starts one named startup task at a time; task rows have their own stop controls.'),
        'consoles-start':('Startup','Initializes console support and tools.'),
        'updates':('Updates','Checks for source updates and coordinates reloads.'),
        'permuter-output':('Permuter','Reads output from a permutation subprocess.'),
    }
    if name in exact:
        return exact[name]
    prefixes = [
        ('startup-task-','Startup task','Runs one optional startup scan; stop it using its named task row.'),
        ('background-worker','Background pool','Shared machine-draft and permutation worker; may be idle.'),
        ('progress-compile','Saved progress','Compiles saved C to verify imported or synchronized matches.'),
        ('deep-pass','Deep permuter','Searches C rewrites for a function during a deep pass.'),
        ('iterative-repair','Local repairs','Tests successive local source repairs.'),
        ('room-heartbeat-','Room heartbeat','Keeps a room connection alive and reports presence.'),
        ('link-','Room synchronization','Exchanges room contributions and connection state.'),
        ('collab-','Room server','Serves requests from room participants.'),
        ('agent-','Agents','Runs an agent session and handles its tool exchanges.'),
        ('drafts-','Machine drafts','Schedules draft generation and detects changed inputs.'),
        ('permuter-','Permuter','Schedules searches for near-matching functions.'),
        ('autopilot-','Autopilot','Tries generated drafts and coordinates automatic matching.'),
        ('twins-','Code reuse','Checks identical functions and reuses verified sources.'),
        ('range-check-','Compiler evidence','Checks compiler-range hypotheses against neighboring functions.'),
        ('file-check-','Compiler evidence','Checks compiler settings for source files.'),
        ('file-survey-','Compiler survey','Tests compiler settings across source files.'),
        ('survey-','Compiler survey','Tests likely compiler profiles.'),
        ('compiler-status-','Compiler evidence','Refreshes automatic surveys and range checks without blocking the compiler panel.'),
        ('compiler-map-','Compiler analysis','Builds the project compiler map.'),
        ('briefs-','Agent preparation','Prepares function context for agents.'),
        ('preflight-','Project preparation','Checks project build prerequisites.'),
        ('index-','Project indexing','Indexes project sources and symbols.'),
        ('job-','Background job','Runs background work or handles agent process completion; see the activity list above.'),
        ('ThreadPoolExecutor-','Shared executor','Executes queued app tasks; its assignment can change between requests.'),
        ('source-','Source updates','Prepares or downloads the source runtime.'),
        ('window','Window bridge','Communicates with or watches the separate app window process.'),
        ('tray','Tray','Coordinates the tray icon and its events.'),
        ('update','Updates','Downloads or prepares an app update.'),
    ]
    for prefix, role, text in prefixes:
        if name.startswith(prefix):
            return role, text
    return 'Other', 'No registered description for this thread. It may belong to a library.'


class Sampler:
    def __init__(self):
        self.read = cpu_reader()
        self.previous = {}
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            live = [t for t in threading.enumerate() if t.is_alive()]
            rows, updated = [], {}
            for thread in live:
                tid = thread.native_id
                cpu = self.read(tid) if tid is not None else None
                old = self.previous.get(thread)
                percent = None
                if cpu is not None:
                    if old and now-old[0] < 1:
                        updated[thread] = old
                        percent = old[2]
                    else:
                        if old and cpu >= old[1]:
                            percent = 100*(cpu-old[1])/(now-old[0])
                        updated[thread] = (now,cpu,percent)
                role, description = purpose(thread.name)
                rows.append(dict(name=thread.name, native_id=tid, role=role, purpose=description,
                                 cpu_percent=percent, cpu_state='unavailable' if cpu is None else 'measuring' if percent is None else 'measured'))
            self.previous = updated  # Prune finished threads; a replacement thread starts a new baseline.
            return sorted(rows,key=lambda r:(r['name'],r['native_id'] or 0))
