/* The browser build has no signals: the host's PORT_SAMPLE backtraces are native only. */
#ifndef PORT_WEB_SIGNAL_H
#define PORT_WEB_SIGNAL_H
#define SIGUSR1 0
#define signal(sig, handler) ((void)(sig), (void)(handler))
#endif
