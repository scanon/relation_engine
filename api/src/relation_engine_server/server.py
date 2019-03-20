"""The main entrypoint for running the Flask server."""
import re
import flask
import json
import os
from uuid import uuid4
import traceback
from jsonschema.exceptions import ValidationError

from .exceptions import MissingHeader, UnauthorizedAccess, InvalidParameters
from .utils import arango_client, spec_loader
from .api_modules import api_v1

# All api version modules, from oldest to newest
_API_VERSIONS = [api_v1.endpoints]

app = flask.Flask(__name__)
app.config['DEBUG'] = os.environ.get('FLASK_DEBUG', True)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', str(uuid4()))
app.url_map.strict_slashes = False  # allow both `get /v1/` and `get /v1`


@app.route('/', methods=['GET'])
def root():
    """Server status."""
    if os.path.exists('.git/refs/heads/master'):
        with open('.git/refs/heads/master', 'r') as fd:
            commit_hash = fd.read().strip()
    else:
        commit_hash = 'unknown'
    arangodb_status = arango_client.server_status()
    repo_url = 'https://github.com/kbase/relation_engine_api.git'
    body = {
        'arangodb_status': arangodb_status,
        'commit_hash': commit_hash,
        'repo_url': repo_url
    }
    return flask.jsonify(body)


@app.route('/api/<path:path>', methods=['GET', 'PUT', 'POST', 'DELETE'])
def api_call(path):
    """
    Handle an api request, dispatching it to the appropriate versioned module.

    Versioning system:
    - Every API version is a discrete python module that contains an 'endpoints' dictionary.
    - Versions are simple incrementing integers. We only need a new version for breaking changes.
    """
    # Get the path and version number
    path_parts = path.split('/')
    (version_int, api_path) = _get_version_and_path(path_parts)
    # Find our method in the various versioned modules
    # Note: the mypy type checker has difficulties with the endpoints dict, so we ignore type checking below
    endpoints = _API_VERSIONS[version_int - 1]  # index 0 == version 1
    if api_path not in endpoints:
        body = {'error': f'Path not found: {api_path}.'}
        return (flask.jsonify(body), 404)
    methods = endpoints[api_path].get('methods', {'GET'})  # type: ignore
    # Mypy is not able to infer that `methods` will always be a set
    if flask.request.method not in methods:  # type: ignore
        return (flask.jsonify({'error': '405 - Method not allowed.'}), 405)
    # We found a matching function for the endpoint and method
    # Mypy is not able to infer that this is a function
    result = endpoints[api_path]['handler']()  # type: ignore
    return (flask.jsonify(result), 200)


def _get_version_and_path(path_parts):
    """
    From a list of path parts, initialize and validate a version int for the api.
    Returns pair of (version_int, path_str)
    """
    version_str = path_parts[0]
    max_version = len(_API_VERSIONS)
    # Make sure the version looks like 'v12'
    if not re.match(r'^v\d+$', version_str):
        # Fallback to v1 for paths like /api/<path...> with no version option
        # TODO temporary
        return (1, '/'.join(path_parts))
        # raise InvalidParameters('Make a request with the format /api/<version>/<path...>')
    # Parse to an int
    version_int = int(version_str.replace('v', ''))
    # Make sure the version number is valid
    if version_int <= 0:
        raise InvalidParameters('API version must be > 0')
    if version_int > max_version:
        raise InvalidParameters(f'Invalid api version; max is {max_version}')
    path_str = '/'.join(path_parts[1:])
    return (version_int, path_str)


@app.errorhandler(json.decoder.JSONDecodeError)
def json_decode_error(err):
    """A problem parsing json."""
    resp = {
        'error': 'Unable to parse JSON',
        'source_json': err.doc,
        'pos': err.pos,
        'lineno': err.lineno,
        'colno': err.colno
    }
    return (flask.jsonify(resp), 400)


@app.errorhandler(arango_client.ArangoServerError)
def arango_server_error(err):
    resp = {
        'error': str(err),
        'arango_message': err.resp_json['errorMessage']
    }
    return (flask.jsonify(resp), 400)


@app.errorhandler(InvalidParameters)
def invalid_params(err):
    """Invalid request body json params."""
    resp = {'error': str(err)}
    return (flask.jsonify(resp), 400)


@app.errorhandler(spec_loader.SchemaNonexistent)
@app.errorhandler(spec_loader.ViewNonexistent)
def view_does_not_exist(err):
    """General error cases."""
    resp = {'error': str(err), 'name': err.name}
    return (flask.jsonify(resp), 400)


@app.errorhandler(ValidationError)
def validation_error(err):
    """Json Schema validation error."""
    resp = {
        'error': str(err).split('\n')[0],
        'instance': err.instance,
        'validator': err.validator,
        'validator_value': err.validator_value,
        'schema': err.schema
    }
    return (flask.jsonify(resp), 400)


@app.errorhandler(UnauthorizedAccess)
def unauthorized_access(err):
    resp = {
        'error': '403 - Unauthorized',
        'auth_url': err.auth_url,
        'auth_response': err.response
    }
    return (flask.jsonify(resp), 403)


@app.errorhandler(404)
def page_not_found(err):
    return (flask.jsonify({'error': '404 - Not found.'}), 404)


@app.errorhandler(405)
def method_not_allowed(err):
    return (flask.jsonify({'error': '405 - Method not allowed.'}), 405)


@app.errorhandler(MissingHeader)
def generic_400(err):
    return (flask.jsonify({'error': str(err)}), 400)


# Any other unhandled exceptions -> 500
@app.errorhandler(Exception)
@app.errorhandler(500)
def server_error(err):
    print('=' * 80)
    print('500 Unexpected Server Error')
    print('-' * 80)
    traceback.print_exc()
    print('=' * 80)
    resp = {'error': '500 - Unexpected server error'}
    resp['error_class'] = err.__class__.__name__
    resp['error_details'] = str(err)
    return (flask.jsonify(resp), 500)


@app.after_request
def after_request(resp):
    # Log request
    print(' '.join([flask.request.method, flask.request.path, '->', resp.status]))
    # Enable CORS
    resp.headers['Access-Control-Allow-Origin'] = '*'
    env_allowed_headers = os.environ.get('HTTP_ACCESS_CONTROL_REQUEST_HEADERS', 'Authorization, Content-Type')
    resp.headers['Access-Control-Allow-Headers'] = env_allowed_headers
    # Set JSON content type and response length
    resp.headers['Content-Type'] = 'application/json'
    resp.headers['Content-Length'] = resp.calculate_content_length()
    return resp
