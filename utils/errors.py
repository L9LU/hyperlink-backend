# utils/errors.py
# One place to turn an unexpected crash into a safe reply.
#
# Routes used to answer a crash with the raw Python error text, which can include
# database messages and internal details. Now the full error goes to the server
# log (Render > Logs) and the user gets a short, generic message.

import logging

from flask import jsonify, request
from werkzeug.exceptions import HTTPException

log = logging.getLogger('hyperlink')


def server_error(e):
    """Use in a route's `except Exception as e:` block:  return server_error(e)"""
    # Deliberate HTTP errors (413 too large, 404, 405 ...) keep their own status
    # and go through the app's JSON handlers instead of becoming a 500.
    if isinstance(e, HTTPException):
        raise e

    log.exception('Unhandled error on %s %s', request.method, request.path)
    return jsonify({'error': 'Something went wrong on our side. Please try again.'}), 500
