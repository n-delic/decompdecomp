"""Save user-requested metrics independently of embedded browser download support."""
import re
import uuid
from pathlib import Path


def save(slug, csv):
    if not isinstance(csv, str) or not csv.startswith('"schema_version",') or len(csv.encode('utf-8')) > 2_000_000:
        raise ValueError('Export requires a metrics CSV smaller than 2 MB. Refresh the metrics and try again.')
    directory = Path.home() / 'Downloads'
    name = re.sub(r'[^a-zA-Z0-9_-]', '_', slug)[:80] or 'project'
    path = directory / f'{name}-rewrite-metrics-{uuid.uuid4().hex[:8]}.csv'
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='') as output:
            output.write(csv)
    except OSError as error:
        raise ValueError(f'Could not save metrics in {directory}: {error}. Check that Downloads is writable.') from error
    return {'path': str(path)}
