"""Compare frozen judging inputs without confusing image-container provenance with machine endianness."""
import re


def complete_snapshot(settings):
    """Only complete compiler-invocation evidence permits ignoring container metadata."""
    if not isinstance(settings, dict):
        return False
    rom = settings.get('rom') or {}
    compiler = settings.get('compiler') or {}
    return (settings.get('mode') == 'rom'
            and settings.get('settings_source') == 'compile invocation snapshot'
            and isinstance(settings.get('big_endian'), bool)
            and settings.get('language') in ('c', 'c++')
            and isinstance(settings.get('context_unit'), str)
            and isinstance(rom, dict) and bool(re.fullmatch(r'[0-9a-f]{40}', str(rom.get('sha1', ''))))
            and isinstance(rom.get('byte_order'), str) and bool(rom['byte_order'])
            and bool(re.fullmatch(r'[0-9a-f]{64}', str(settings.get('context_sha256', ''))))
            and bool(re.fullmatch(r'[0-9a-f]{64}', str(settings.get('scoring_revision', ''))))
            and isinstance(compiler, dict) and bool(compiler.get('toolchain'))
            and isinstance(compiler.get('flags'), list)
            and isinstance(settings.get('effective_command'), list) and bool(settings['effective_command']))


def differences(before, after):
    """Retain every input difference except original container order for the same verified image.

    Actual processor byte order remains in big_endian. No raw snapshot is changed;
    incomplete/older snapshots and project builds keep strict equality semantics.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {'settings'}
    if (complete_snapshot(before) and complete_snapshot(after)
            and before['rom']['sha1'] == after['rom']['sha1']):
        before = {**before, 'rom': {k:v for k,v in before['rom'].items() if k != 'byte_order'}}
        after = {**after, 'rom': {k:v for k,v in after['rom'].items() if k != 'byte_order'}}
    missing = object()
    return {key for key in before.keys() | after.keys()
            if before.get(key, missing) != after.get(key, missing)}
