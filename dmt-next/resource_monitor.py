"""Resource sampling in a thread, including during synchronous Qt calculations."""
import threading
import time


def start_monitor(emit):
    stopped = threading.Event()
    def sample():
        try:
            import psutil
            process = psutil.Process()
            process.cpu_percent()
            previous = psutil.net_io_counters()
            before = time.monotonic()
            while not stopped.wait(1):
                now = time.monotonic()
                network = psutil.net_io_counters()
                io = process.io_counters()
                emit({'event': 'resources', 'resources': {
                    'pid': process.pid, 'timestamp': time.time(),
                    'rssBytes': process.memory_info().rss,
                    'processCpuPercent': process.cpu_percent(),
                    'logicalCpus': psutil.cpu_count() or 1,
                    'readBytes': io.read_bytes, 'writeBytes': io.write_bytes,
                    'hostReceiveBytesPerSecond': max(0, network.bytes_recv-previous.bytes_recv)/(now-before),
                    'hostSendBytesPerSecond': max(0, network.bytes_sent-previous.bytes_sent)/(now-before),
                }})
                previous, before = network, now
        except Exception as exc:
            emit({'event': 'resources-unavailable', 'error': str(exc)})
    threading.Thread(target=sample, daemon=True).start()
    return stopped
