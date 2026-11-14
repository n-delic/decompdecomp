"""Owner-approved, bounded reference batches; a stop never cancels an admitted judge."""

from __future__ import annotations

from functools import partial

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...network import NetworkError
from ...reference_batches import ReferenceBatches
from . import App


def register(app: App) -> None:
    service = ReferenceBatches(app.net)
    mcp, guard, body, call, fail = app.mcp, app.guard, app.body, app.call, app.fail

    @mcp.custom_route('/api/reference-batches', methods=['GET'])
    async def history(request: Request) -> Response:
        if bad := guard(request):
            return bad
        try:
            limit = _query_int(request, 'limit', 10, 1, 50)
            return JSONResponse(await call(service.history, request.query_params.get('slug', ''), limit))
        except (NetworkError, ValueError, OSError, RuntimeError) as error:
            return fail(error)

    @mcp.custom_route('/api/reference-batches/detail', methods=['GET'])
    async def detail(request: Request) -> Response:
        if bad := guard(request):
            return bad
        try:
            batch_id = _query_int(request, 'id', 0, 1, 2**63 - 1)
            return JSONResponse(await call(service.detail, request.query_params.get('slug', ''), batch_id))
        except (NetworkError, ValueError, OSError, RuntimeError) as error:
            return fail(error)

    @mcp.custom_route('/api/reference-batches/preview', methods=['POST'])
    async def preview(request: Request) -> Response:
        def operation(data):
            _keys(data, {'slug'}, {'slug', 'max_trials', 'max_seconds'})
            trials = _integer(data.get('max_trials', 10), 'max_trials', 1, 20)
            seconds = _integer(data.get('max_seconds', 900), 'max_seconds', 60, 3600)
            return call(service.preview, _slug(data), trials, seconds)
        return await post(request, operation)

    @mcp.custom_route('/api/reference-batches/run', methods=['POST'])
    async def run(request: Request) -> Response:
        async def operation(data):
            _keys(data, {'slug', 'plan', 'candidate_ids', 'accept_if_verified', 'allow_progress_copy'})
            slug = _slug(data)
            plan = data['plan']
            ids = data['candidate_ids']
            if not isinstance(plan, str) or not plan or len(plan.encode('utf-8')) > 32768:
                raise ValueError('Use a bounded, freshly reviewed batch plan.')
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
                    or any(not isinstance(value, str) or len(value) > 64 for value in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError('Select 1–20 distinct reviewed candidate IDs.')
            if data['accept_if_verified'] is not True or data['allow_progress_copy'] is not True:
                raise ValueError('Both batch consent flags must be true; review the candidates first.')
            reservation = await call(service.admit, slug, plan, ids, True, True)
            batch = reservation['batch']
            job = reservation.get('job')
            if reservation['created']:
                try:
                    # Jobs' generic cancellation can erase the result after a commit.
                    # Only the batch's durable stop-after-current flag may stop this work.
                    job = app.jobs.start('reference-batch', partial(service.execute, slug, batch['id']),
                                         cancellable=False)
                    await call(service.attach_job, slug, batch['id'], job)
                except (OSError, RuntimeError, ValueError, NetworkError):
                    await call(service.stop, slug, batch['id'])
                    raise
                batch = (await call(service.detail, slug, batch['id']))['batch']
            return {'job': job, 'batch': batch}
        return await post(request, operation)

    @mcp.custom_route('/api/reference-batches/stop', methods=['POST'])
    async def stop(request: Request) -> Response:
        def operation(data):
            _keys(data, {'slug', 'id'})
            batch_id = _integer(data['id'], 'id', 1, 2**63 - 1)
            return call(service.stop, _slug(data), batch_id)
        return await post(request, operation)

    async def post(request: Request, operation) -> Response:
        if bad := guard(request, write=True):
            return bad
        try:
            data = await body(request)
            if not isinstance(data, dict):
                raise ValueError('Send a JSON object containing the documented batch fields.')
            return JSONResponse(await operation(data))
        except (NetworkError, ValueError, OSError, RuntimeError, TypeError) as error:
            return fail(error)


def _keys(data, required, allowed=None):
    allowed = required if allowed is None else allowed
    if not required <= data.keys() or set(data) - allowed:
        raise ValueError('Use exactly the documented reference-batch fields.')


def _slug(data):
    slug = data.get('slug')
    if not isinstance(slug, str) or not slug or len(slug) > 256:
        raise ValueError('Choose a valid local project.')
    return slug


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer between {low} and {high}.')
    return value


def _query_int(request, name, default, low, high):
    value = request.query_params.get(name)
    try:
        number = int(value) if value is not None else default
    except (ValueError, TypeError):
        raise ValueError(f'{name} must be an integer.') from None
    return _integer(number, name, low, high)
