"""
Flask API for the Apex Dashboard to query the SQLite database.
Runs alongside the Discord bot.
"""

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from functools import wraps
import os
import hmac
import hashlib
import shutil
import asyncio
import sys
import time
import platform
import resource
from datetime import datetime

import discord

# Import the database
from database import db

# Bot reference for profile updates (set by bot.py)
_bot = None

def log(msg):
    print(msg, file=sys.stderr, flush=True)

def set_bot_reference(bot):
    global _bot
    _bot = bot
    log(f"[API] Bot reference set: bot={bot}, guilds={len(bot.guilds)}")

app = Flask(__name__)
CORS(app)  # Allow cross-origin requests from dashboard

# API secret for authentication (set in environment)
API_SECRET = os.getenv('BOT_API_SECRET', 'change-this-secret-in-production')

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Unauthorized'}), 401

        token = auth_header.split(' ')[1]
        if not hmac.compare_digest(token, API_SECRET):
            return jsonify({'error': 'Invalid token'}), 401

        return f(*args, **kwargs)
    return decorated

_start_time = time.time()

@app.route('/health', methods=['GET'])
@require_auth
def health():
    request_start = time.time()
    now = time.time()
    uptime_seconds = now - _start_time

    bot_online = False
    ws_latency = None
    total_servers = 0
    total_users = 0
    if _bot is not None:
        try:
            last_heartbeat = _bot._connection._heartbeat_start if hasattr(_bot._connection, '_heartbeat_start') else None
            if last_heartbeat and (now - last_heartbeat) < 120:
                bot_online = True
            elif _bot.latency and _bot.latency < 120:
                bot_online = True
            ws_latency = round(_bot.latency * 1000, 2) if _bot.latency else None
            total_servers = len(_bot.guilds)
            total_users = sum(g.member_count or 0 for g in _bot.guilds)
        except Exception:
            pass

    db_healthy = False
    db_latency_ms = None
    try:
        db_start = time.time()
        db.get_bot_status()
        db_latency_ms = round((time.time() - db_start) * 1000, 2)
        db_healthy = True
    except Exception:
        pass

    bot_status_data = {}
    try:
        bot_status_data = db.get_bot_status() or {}
    except Exception:
        pass

    mem_mb = None
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == 'darwin':
            mem_mb = round(usage.ru_maxrss / (1024 * 1024), 2)
        else:
            mem_mb = round(usage.ru_maxrss / 1024, 2)
    except Exception:
        pass

    api_response_ms = round((time.time() - request_start) * 1000, 2)

    return jsonify({
        'bot': {
            'status': 'online' if bot_online else 'offline',
            'websocket_latency_ms': ws_latency,
            'uptime_seconds': round(uptime_seconds, 2),
            'total_servers': total_servers,
            'total_users': total_users,
            'commands_run': bot_status_data.get('commands_run', 0),
        },
        'database': {
            'healthy': db_healthy,
            'latency_ms': db_latency_ms,
        },
        'system': {
            'api_response_ms': api_response_ms,
            'memory_mb': mem_mb,
            'python_version': platform.python_version(),
            'discord_py_version': discord.__version__,
        },
    })

@app.route('/api/health', methods=['GET'])
def public_health():
    request_start = time.time()
    uptime_seconds = time.time() - _start_time

    ws_latency = None
    bot_ok = False
    if _bot is not None:
        try:
            ws_latency = round(_bot.latency * 1000, 2) if _bot.latency else None
            bot_ok = _bot.latency is not None and _bot.latency < 120
        except Exception:
            pass

    db_ok = False
    try:
        db.get_bot_status()
        db_ok = True
    except Exception:
        pass

    if bot_ok and db_ok:
        status = 'healthy'
    elif bot_ok or db_ok:
        status = 'degraded'
    else:
        status = 'down'

    return jsonify({
        'status': status,
        'latency_ms': ws_latency,
        'uptime_seconds': round(uptime_seconds, 2),
        'api_response_ms': round((time.time() - request_start) * 1000, 2),
    })


@app.route('/api/bot/status', methods=['GET'])
@require_auth
def get_bot_status():
    status = db.get_bot_status()
    return jsonify(status)

@app.route('/api/bot/guilds', methods=['GET'])
@require_auth
def get_bot_guilds():
    guilds = db.get_all_bot_guilds()
    return jsonify({'guilds': guilds})


@app.route('/api/guilds/<guild_id>/settings', methods=['GET'])
@require_auth
def get_guild_settings(guild_id):
    settings = db.get_guild_settings(guild_id)
    return jsonify(settings)

@app.route('/api/guilds/<guild_id>/settings', methods=['PATCH'])
@require_auth
def update_guild_settings(guild_id):
    data = request.get_json()
    db.update_guild_settings(guild_id, data)
    return jsonify({'success': True})

@app.route('/api/guilds/<guild_id>/apply-bot-profile', methods=['POST'])
@require_auth
def apply_bot_profile_endpoint(guild_id):
    """Apply bot profile settings (nickname, avatar) to the guild."""
    global _bot
    log(f"[Bot Profile] Endpoint called for guild {guild_id}, _bot={_bot is not None}")

    if not _bot:
        log(f"[Bot Profile] Bot not initialized")
        return jsonify({'error': 'Bot not initialized'}), 503

    try:
        # Import the apply function from bot module
        from helpers.utils import apply_bot_profile

        log(f"[Bot Profile] Running apply_bot_profile coroutine")
        log(f"[Bot Profile] Bot guilds: {len(_bot.guilds)}")

        # Run the async function in the bot's event loop, passing bot instance
        future = asyncio.run_coroutine_threadsafe(
            apply_bot_profile(int(guild_id), _bot),
            _bot.loop
        )
        result = future.result(timeout=30)  # 30 second timeout
        log(f"[Bot Profile] Result: {result}")

        if result.get('success'):
            log(f"[Bot Profile] Successfully applied profile for guild {guild_id}")
            return jsonify({'success': True})
        else:
            error = result.get('error', 'Unknown error')
            log(f"[Bot Profile] Failed for guild {guild_id}: {error}")
            return jsonify({'error': error}), 400
    except Exception as e:
        log(f"[Bot Profile] Exception for guild {guild_id}: {str(e)}")
        import traceback
        log(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/guilds/<guild_id>/logs', methods=['GET'])
@require_auth
def get_mod_logs(guild_id):
    limit = request.args.get('limit', 50, type=int)
    logs = db.get_mod_logs(guild_id, limit)
    return jsonify({'logs': logs})


@app.route('/api/guilds/<guild_id>/warnings', methods=['GET'])
@require_auth
def get_warnings(guild_id):
    user_id = request.args.get('userId')
    if user_id:
        warnings = db.get_user_warnings(guild_id, user_id)
    else:
        warnings = db.get_all_warnings(guild_id)
    return jsonify({'warnings': warnings})


@app.route('/api/guilds/<guild_id>/tickets', methods=['GET'])
@require_auth
def get_tickets(guild_id):
    status = request.args.get('status')
    tickets = db.get_all_tickets(guild_id, status)
    return jsonify({'tickets': tickets})


@app.route('/api/guilds/<guild_id>/ticket-panels', methods=['GET'])
@require_auth
def get_ticket_panels(guild_id):
    panels = db.get_all_ticket_panels(guild_id)
    return jsonify({'panels': panels})

@app.route('/api/guilds/<guild_id>/ticket-panels', methods=['POST'])
@require_auth
def create_ticket_panel(guild_id):
    data = request.get_json()
    panel_id = db.create_ticket_panel(guild_id, data)
    return jsonify({'id': panel_id, 'success': True})

@app.route('/api/guilds/<guild_id>/ticket-panels/<panel_id>', methods=['GET'])
@require_auth
def get_ticket_panel(guild_id, panel_id):
    panel = db.get_ticket_panel(guild_id, panel_id)
    if not panel:
        return jsonify({'error': 'Panel not found'}), 404
    return jsonify(panel)

@app.route('/api/guilds/<guild_id>/ticket-panels/<panel_id>', methods=['PATCH'])
@require_auth
def update_ticket_panel(guild_id, panel_id):
    data = request.get_json()
    db.update_ticket_panel(guild_id, panel_id, data)
    return jsonify({'success': True})

@app.route('/api/guilds/<guild_id>/ticket-panels/<panel_id>', methods=['DELETE'])
@require_auth
def delete_ticket_panel(guild_id, panel_id):
    db.delete_ticket_panel(guild_id, panel_id)
    return jsonify({'success': True})


@app.route('/api/guilds/<guild_id>/transcripts', methods=['GET'])
@require_auth
def get_transcripts(guild_id):
    limit = request.args.get('limit', 50, type=int)
    transcripts = db.get_all_transcripts(guild_id, limit)
    return jsonify({'transcripts': transcripts})

@app.route('/api/guilds/<guild_id>/transcripts/<transcript_id>', methods=['GET'])
@require_auth
def get_transcript(guild_id, transcript_id):
    transcript = db.get_transcript(guild_id, transcript_id)
    if not transcript:
        return jsonify({'error': 'Transcript not found'}), 404
    return jsonify(transcript)


@app.route('/api/guilds/<guild_id>/custom-commands', methods=['GET'])
@require_auth
def get_custom_commands(guild_id):
    commands = db.get_all_custom_commands(guild_id)
    return jsonify({'commands': commands})

@app.route('/api/guilds/<guild_id>/custom-commands', methods=['POST'])
@require_auth
def create_custom_command(guild_id):
    data = request.get_json()
    cmd_id = db.create_custom_command(guild_id, data)
    return jsonify({'id': cmd_id, 'success': True})

@app.route('/api/guilds/<guild_id>/custom-commands/<name>', methods=['DELETE'])
@require_auth
def delete_custom_command(guild_id, name):
    db.delete_custom_command(guild_id, name)
    return jsonify({'success': True})


@app.route('/api/guilds/<guild_id>/reaction-roles', methods=['GET'])
@require_auth
def get_reaction_roles(guild_id):
    panels = db.get_all_reaction_role_panels(guild_id)
    return jsonify({'panels': panels})

@app.route('/api/guilds/<guild_id>/reaction-roles', methods=['POST'])
@require_auth
def create_reaction_role(guild_id):
    data = request.get_json()
    panel_id = db.create_reaction_role_panel(guild_id, data)
    return jsonify({'id': panel_id, 'success': True})

@app.route('/api/guilds/<guild_id>/reaction-roles/<panel_id>', methods=['GET'])
@require_auth
def get_reaction_role(guild_id, panel_id):
    panel = db.get_reaction_role_panel(guild_id, panel_id)
    if not panel:
        return jsonify({'error': 'Panel not found'}), 404
    return jsonify(panel)

@app.route('/api/guilds/<guild_id>/reaction-roles/<panel_id>', methods=['PATCH'])
@require_auth
def update_reaction_role(guild_id, panel_id):
    data = request.get_json()
    db.update_reaction_role_panel(guild_id, panel_id, data)
    return jsonify({'success': True})

@app.route('/api/guilds/<guild_id>/reaction-roles/<panel_id>', methods=['DELETE'])
@require_auth
def delete_reaction_role(guild_id, panel_id):
    db.delete_reaction_role_panel(guild_id, panel_id)
    return jsonify({'success': True})


@app.route('/api/blacklist', methods=['GET'])
@require_auth
def get_blacklist():
    blacklist = db.get_blacklist()
    return jsonify({'blacklist': blacklist})

@app.route('/api/blacklist', methods=['POST'])
@require_auth
def add_to_blacklist():
    data = request.get_json()
    user_id = data.get('userId')
    reason = data.get('reason', 'No reason provided')
    added_by = data.get('addedBy', 'dashboard')
    if not user_id:
        return jsonify({'error': 'User ID required'}), 400
    db.add_to_blacklist(user_id, reason, added_by)
    return jsonify({'success': True})

@app.route('/api/blacklist/<user_id>', methods=['DELETE'])
@require_auth
def remove_from_blacklist(user_id):
    db.remove_from_blacklist(user_id)
    return jsonify({'success': True})


@app.route('/api/knowledge-base', methods=['GET'])
@require_auth
def get_faqs():
    faqs = db.get_all_faqs()
    return jsonify({'faqs': faqs})

@app.route('/api/knowledge-base', methods=['POST'])
@require_auth
def create_faq():
    data = request.get_json()
    faq_id = db.create_faq(data)
    return jsonify({'id': faq_id, 'success': True})

@app.route('/api/knowledge-base/<faq_id>', methods=['PATCH'])
@require_auth
def update_faq(faq_id):
    data = request.get_json()
    db.update_faq(faq_id, data)
    return jsonify({'success': True})

@app.route('/api/knowledge-base/<faq_id>', methods=['DELETE'])
@require_auth
def delete_faq(faq_id):
    db.delete_faq(faq_id)
    return jsonify({'success': True})


@app.route('/api/templates', methods=['GET'])
@require_auth
def get_templates():
    templates = db.get_all_templates()
    return jsonify({'templates': templates})

@app.route('/api/templates', methods=['POST'])
@require_auth
def create_template():
    data = request.get_json()
    template_id = db.create_template(data)
    return jsonify({'id': template_id, 'success': True})

@app.route('/api/templates/<template_id>', methods=['PATCH'])
@require_auth
def update_template(template_id):
    data = request.get_json()
    db.update_template(template_id, data)
    return jsonify({'success': True})

@app.route('/api/templates/<template_id>', methods=['DELETE'])
@require_auth
def delete_template(template_id):
    db.delete_template(template_id)
    return jsonify({'success': True})


@app.route('/api/user-notes', methods=['GET'])
@require_auth
def get_user_notes():
    user_id = request.args.get('userId')
    if not user_id:
        return jsonify({'error': 'User ID required'}), 400
    notes = db.get_user_notes(user_id)
    return jsonify({'notes': notes})

@app.route('/api/user-notes', methods=['POST'])
@require_auth
def add_user_note():
    data = request.get_json()
    user_id = data.get('userId')
    note = data.get('note')
    added_by = data.get('addedBy', 'dashboard')
    if not user_id or not note:
        return jsonify({'error': 'User ID and note required'}), 400
    note_id = db.add_user_note(user_id, note, added_by)
    return jsonify({'id': note_id, 'success': True})

@app.route('/api/user-notes/<note_id>', methods=['DELETE'])
@require_auth
def delete_user_note(note_id):
    db.delete_user_note(note_id)
    return jsonify({'success': True})


@app.route('/api/audit-logs', methods=['GET'])
@require_auth
def get_audit_logs():
    staff_id = request.args.get('staffId')
    limit = request.args.get('limit', 50, type=int)
    logs = db.get_audit_logs(staff_id, limit)
    return jsonify({'logs': logs})

@app.route('/api/audit-logs', methods=['POST'])
@require_auth
def add_audit_log():
    data = request.get_json()
    log_id = db.add_audit_log(data)
    return jsonify({'id': log_id, 'success': True})


@app.route('/api/server-health', methods=['GET'])
@require_auth
def get_server_health():
    servers = db.get_all_server_health()
    unhealthy = [s for s in servers if s['issues_count'] > 0]
    healthy = [s for s in servers if s['issues_count'] == 0]
    return jsonify({
        'servers': servers,
        'unhealthy_count': len(unhealthy),
        'healthy_count': len(healthy),
        'total': len(servers)
    })


@app.route('/api/error-logs', methods=['GET'])
@require_auth
def get_error_logs():
    limit = request.args.get('limit', 50, type=int)
    logs = db.get_error_logs(limit)
    return jsonify({'logs': logs})

@app.route('/api/error-logs/<log_id>', methods=['DELETE'])
@require_auth
def delete_error_log(log_id):
    db.delete_error_log(log_id)
    return jsonify({'success': True})


@app.route('/api/guilds/<guild_id>/suggestions', methods=['GET'])
@require_auth
def get_suggestions(guild_id):
    status = request.args.get('status')
    limit = request.args.get('limit', 50, type=int)
    suggestions = db.get_suggestions(guild_id, status, limit)
    return jsonify({'suggestions': suggestions})

@app.route('/api/guilds/<guild_id>/suggestions', methods=['POST'])
@require_auth
def create_suggestion(guild_id):
    data = request.get_json()
    suggestion_id = db.create_suggestion(guild_id, data)
    return jsonify({'id': suggestion_id, 'success': True})

@app.route('/api/guilds/<guild_id>/suggestions/<suggestion_id>', methods=['PATCH'])
@require_auth
def update_suggestion(guild_id, suggestion_id):
    data = request.get_json()
    db.update_suggestion(guild_id, suggestion_id, data)
    return jsonify({'success': True})

@app.route('/api/guilds/<guild_id>/suggestions/<suggestion_id>', methods=['DELETE'])
@require_auth
def delete_suggestion(guild_id, suggestion_id):
    db.delete_suggestion(guild_id, suggestion_id)
    return jsonify({'success': True})


@app.route('/api/guilds/<guild_id>/auto-responders', methods=['GET'])
@require_auth
def get_auto_responders(guild_id):
    responders = db.get_auto_responders(guild_id)
    return jsonify({'responders': responders})

@app.route('/api/guilds/<guild_id>/auto-responders', methods=['POST'])
@require_auth
def create_auto_responder(guild_id):
    data = request.get_json()
    responder_id = db.create_auto_responder(guild_id, data)
    return jsonify({'id': responder_id, 'success': True})

@app.route('/api/guilds/<guild_id>/auto-responders/<responder_id>', methods=['PATCH'])
@require_auth
def update_auto_responder(guild_id, responder_id):
    data = request.get_json()
    db.update_auto_responder(guild_id, responder_id, data)
    return jsonify({'success': True})

@app.route('/api/guilds/<guild_id>/auto-responders/<responder_id>', methods=['DELETE'])
@require_auth
def delete_auto_responder(guild_id, responder_id):
    db.delete_auto_responder(guild_id, responder_id)
    return jsonify({'success': True})


@app.route('/api/stats', methods=['GET'])
@require_auth
def get_stats():
    bot_guilds = db.get_all_bot_guilds()
    bot_status = db.get_bot_status()

    return jsonify({
        'totalServers': len(bot_guilds),
        'totalWarnings': db.get_total_warnings_count(),
        'totalTickets': db.get_total_tickets_count(),
        'totalTranscripts': db.get_total_transcripts_count(),
        'totalUsers': db.get_total_users_count(),
        'botStatus': bot_status
    })


@app.route('/api/user-lookup/<user_id>', methods=['GET'])
@require_auth
def lookup_user(user_id):
    """Look up a user across all guilds."""
    bot_guilds = db.get_all_bot_guilds()
    guild_names = {g['guild_id']: g['name'] for g in bot_guilds}

    all_warnings = []
    all_tickets = []

    # This is expensive - consider caching or limiting
    for guild in bot_guilds:
        guild_id = guild['guild_id']

        # Get warnings
        warnings = db.get_user_warnings(guild_id, user_id)
        for w in warnings:
            all_warnings.append({
                'guildId': guild_id,
                'guildName': guild_names.get(guild_id, 'Unknown'),
                'warning': w
            })

    return jsonify({
        'userId': user_id,
        'warnings': all_warnings,
        'ticketHistory': all_tickets,
        'totalWarnings': len(all_warnings),
        'totalTickets': len(all_tickets)
    })


@app.route('/api/guilds/<guild_id>/voice-generators', methods=['GET'])
@require_auth
def get_voice_generators(guild_id):
    """Get all voice generators for a guild."""
    generators = db.get_all_voice_generators(guild_id)
    return jsonify({'generators': generators})

@app.route('/api/guilds/<guild_id>/voice-generators', methods=['POST'])
@require_auth
def create_voice_generator(guild_id):
    """Create a new voice generator."""
    data = request.get_json()
    generator_id = db.create_voice_generator(guild_id, data)
    return jsonify({'id': generator_id, 'success': True})

@app.route('/api/guilds/<guild_id>/voice-generators/<generator_id>', methods=['GET'])
@require_auth
def get_voice_generator(guild_id, generator_id):
    """Get a specific voice generator."""
    generator = db.get_voice_generator(guild_id, generator_id)
    if not generator:
        return jsonify({'error': 'Generator not found'}), 404
    return jsonify(generator)

@app.route('/api/guilds/<guild_id>/voice-generators/<generator_id>', methods=['PATCH'])
@require_auth
def update_voice_generator(guild_id, generator_id):
    """Update a voice generator."""
    data = request.get_json()
    db.update_voice_generator(guild_id, generator_id, data)
    return jsonify({'success': True})

@app.route('/api/guilds/<guild_id>/voice-generators/<generator_id>', methods=['DELETE'])
@require_auth
def delete_voice_generator(guild_id, generator_id):
    """Delete a voice generator."""
    db.delete_voice_generator(guild_id, generator_id)
    return jsonify({'success': True})

@app.route('/api/guilds/<guild_id>/temp-voice-channels', methods=['GET'])
@require_auth
def get_temp_voice_channels(guild_id):
    """Get all temporary voice channels for a guild."""
    channels = db.get_all_temp_voice_channels(guild_id)
    return jsonify({'channels': channels})


@app.route('/api/dev/verify-code', methods=['POST'])
@require_auth
def verify_dev_auth_code():
    """Verify a dev auth code and return the guild ID if valid."""
    from datetime import datetime
    data = request.get_json()
    code = data.get('code')
    user_id = data.get('userId')

    if not code or not user_id:
        return jsonify({'error': 'Code and user ID required'}), 400

    # Get the code from database
    auth_code = db.get_dev_auth_code(code)
    if not auth_code:
        return jsonify({'error': 'Invalid code'}), 404

    # Check if all uses consumed
    max_uses = auth_code.get('max_uses', 1)
    use_count = auth_code.get('use_count', 0)
    if use_count >= max_uses:
        return jsonify({'error': 'This code has no remaining uses'}), 400

    # Check if expired
    expires_at = datetime.fromisoformat(auth_code['expires_at'].replace('Z', '+00:00'))
    if datetime.now(expires_at.tzinfo or None) > expires_at:
        return jsonify({'error': 'This code has expired'}), 400

    # Increment use count
    if not db.use_dev_auth_code(code, user_id):
        return jsonify({'error': 'Failed to use code'}), 400

    # Return the guild ID
    return jsonify({
        'success': True,
        'guildId': auth_code['guild_id'],
        'generatedBy': auth_code['user_id']
    })

@app.route('/api/dev/guild-info/<guild_id>', methods=['GET'])
@require_auth
def get_dev_guild_info(guild_id):
    """Get basic guild info for dev dashboard."""
    bot_guilds = db.get_all_bot_guilds()
    guild = next((g for g in bot_guilds if g['guild_id'] == guild_id), None)
    if not guild:
        return jsonify({'error': 'Guild not found'}), 404
    return jsonify({
        'guildId': guild['guild_id'],
        'name': guild.get('name', 'Unknown')
    })


@app.route('/api/incidents', methods=['GET'])
def get_incidents():
    """Get all incidents (public endpoint)."""
    include_resolved = request.args.get('include_resolved', 'true').lower() == 'true'
    incidents = db.get_all_incidents(include_resolved=include_resolved)
    return jsonify({'incidents': incidents})

@app.route('/api/incidents/<incident_id>', methods=['GET'])
def get_incident(incident_id):
    """Get a specific incident (public endpoint)."""
    incident = db.get_incident(incident_id)
    if not incident:
        return jsonify({'error': 'Incident not found'}), 404
    return jsonify({'incident': incident})

@app.route('/api/incidents', methods=['POST'])
@require_auth
def create_incident():
    """Create a new incident (dev only)."""
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    title = data.get('title')
    if not title:
        return jsonify({'error': 'Title is required'}), 400

    incident = db.create_incident(
        title=title,
        description=data.get('description', ''),
        severity=data.get('severity', 'minor'),
        affected_services=data.get('affected_services', []),
        created_by=data.get('created_by')
    )
    return jsonify({'incident': incident}), 201

@app.route('/api/incidents/<incident_id>', methods=['PATCH'])
@require_auth
def update_incident(incident_id):
    """Update an incident (dev only)."""
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    success = db.update_incident(incident_id, data)
    if not success:
        return jsonify({'error': 'Incident not found'}), 404

    incident = db.get_incident(incident_id)
    return jsonify({'incident': incident})

@app.route('/api/incidents/<incident_id>/updates', methods=['POST'])
@require_auth
def add_incident_update(incident_id):
    """Add an update to an incident (dev only)."""
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    message = data.get('message')
    if not message:
        return jsonify({'error': 'Message is required'}), 400

    success = db.add_incident_update(
        incident_id=incident_id,
        message=message,
        status=data.get('status'),
        updated_by=data.get('updated_by')
    )
    if not success:
        return jsonify({'error': 'Incident not found'}), 404

    incident = db.get_incident(incident_id)
    return jsonify({'incident': incident})

@app.route('/api/incidents/<incident_id>', methods=['DELETE'])
@require_auth
def delete_incident(incident_id):
    """Delete an incident (dev only)."""
    success = db.delete_incident(incident_id)
    if not success:
        return jsonify({'error': 'Incident not found'}), 404
    return jsonify({'success': True})


@app.route('/api/database/export', methods=['GET'])
@require_auth
def export_database():
    """Export the database as a .db file (dev only)."""
    import tempfile

    # Get the database file path
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apex.db')

    if not os.path.exists(db_path):
        return jsonify({'error': 'Database file not found'}), 404

    # Create a temporary copy to avoid locking issues
    temp_dir = tempfile.gettempdir()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    temp_path = os.path.join(temp_dir, f'apex_backup_{timestamp}.db')

    try:
        shutil.copy2(db_path, temp_path)
        return send_file(
            temp_path,
            mimetype='application/x-sqlite3',
            as_attachment=True,
            download_name=f'apex_backup_{timestamp}.db'
        )
    except Exception as e:
        return jsonify({'error': f'Failed to export database: {str(e)}'}), 500


@app.route('/api/guilds/<guild_id>/applications', methods=['GET'])
@require_auth
def get_application_forms(guild_id):
    forms = db.get_application_forms(guild_id)
    return jsonify({'forms': forms})

@app.route('/api/guilds/<guild_id>/applications', methods=['POST'])
@require_auth
def create_application_form(guild_id):
    data = request.get_json()
    form_id = db.create_application_form(guild_id, data)
    return jsonify({'id': form_id, 'success': True})

@app.route('/api/guilds/<guild_id>/applications/<form_id>', methods=['PATCH'])
@require_auth
def update_application_form(guild_id, form_id):
    data = request.get_json()
    db.update_application_form(form_id, data)
    return jsonify({'success': True})

@app.route('/api/guilds/<guild_id>/applications/<form_id>', methods=['DELETE'])
@require_auth
def delete_application_form(guild_id, form_id):
    db.delete_application_form(form_id)
    return jsonify({'success': True})

@app.route('/api/applications/<form_id>', methods=['GET'])
def get_public_application_form(form_id):
    form = db.get_application_form(form_id)
    if not form or not form['enabled']:
        return jsonify({'error': 'Form not found'}), 404
    return jsonify({'form': {
        'id': form['id'],
        'guild_id': form['guild_id'],
        'name': form['name'],
        'description': form['description'],
        'banner_url': form.get('banner_url'),
        'questions': form['questions'],
    }})

@app.route('/api/applications/<form_id>/submit', methods=['POST'])
def submit_application(form_id):
    data = request.get_json()
    user_id = data.get('user_id')
    answers = data.get('answers')
    if not user_id or not answers:
        return jsonify({'error': 'Missing user_id or answers'}), 400

    form = db.get_application_form(form_id)
    if not form or not form['enabled']:
        return jsonify({'error': 'Form not found or disabled'}), 404

    existing = db.get_user_pending_application(form_id, user_id)
    if existing:
        return jsonify({'error': 'You already have a pending application'}), 400

    sub_id = db.submit_application(form_id, form['guild_id'], user_id, answers)
    app_settings = form.get('settings', {})

    # Notify via bot if available
    if _bot and _bot.is_ready():
        try:
            import discord

            # Simple notification
            notify_channel = app_settings.get('notify_channel')
            if notify_channel:
                channel = _bot.get_channel(int(notify_channel))
                if channel:
                    embed = discord.Embed(
                        title=f"New Application: {form['name']}",
                        description=f"<@{user_id}> submitted an application.",
                        color=discord.Color.blue(),
                    )
                    embed.set_footer(text=f"ID: {sub_id[:8]}")
                    asyncio.run_coroutine_threadsafe(channel.send(embed=embed), _bot.loop)

            # Full embed with answers
            webhook_channel = app_settings.get('webhook_channel')
            if webhook_channel:
                channel = _bot.get_channel(int(webhook_channel))
                if channel:
                    embed = discord.Embed(
                        title=f"Application: {form['name']}",
                        color=discord.Color.blue(),
                    )
                    embed.set_author(name=f"User ID: {user_id}")
                    for ans in answers:
                        value = ans.get('answer', '')
                        if isinstance(value, list):
                            value = ', '.join(value)
                        embed.add_field(
                            name=ans.get('question', 'Question'),
                            value=str(value)[:1024] or '—',
                            inline=False,
                        )
                    embed.set_footer(text=f"ID: {sub_id[:8]} | Status: Pending")
                    asyncio.run_coroutine_threadsafe(channel.send(embed=embed), _bot.loop)
        except:
            pass

    return jsonify({'id': sub_id, 'success': True})

@app.route('/api/guilds/<guild_id>/applications/<form_id>/submissions', methods=['GET'])
@require_auth
def get_application_submissions(guild_id, form_id):
    status_filter = request.args.get('status')
    subs = db.get_application_submissions(guild_id, form_id, status_filter)
    return jsonify({'submissions': subs})

@app.route('/api/guilds/<guild_id>/applications/submissions/<sub_id>/review', methods=['POST'])
@require_auth
def review_application(guild_id, sub_id):
    data = request.get_json()
    status = data.get('status')
    reviewer_id = data.get('reviewer_id')
    reason = data.get('reason')

    if status not in ('approved', 'denied'):
        return jsonify({'error': 'Status must be approved or denied'}), 400

    sub = db.get_application_submission(sub_id)
    if not sub:
        return jsonify({'error': 'Submission not found'}), 404

    db.review_application(sub_id, status, reviewer_id, reason)

    # DM user and assign role if approved
    if _bot and _bot.is_ready():
        form = db.get_application_form(sub['form_id'])
        app_settings = form.get('settings', {}) if form else {}

        try:
            import discord
            user = _bot.get_user(int(sub['user_id']))
            if not user:
                user = asyncio.run_coroutine_threadsafe(_bot.fetch_user(int(sub['user_id'])), _bot.loop).result(timeout=5)

            if user:
                if status == 'approved':
                    embed = discord.Embed(
                        title=f"Application Approved",
                        description=f"Your application for **{form['name']}** has been approved!",
                        color=discord.Color.green(),
                    )
                    if reason:
                        embed.add_field(name="Note", value=reason, inline=False)

                    # Assign role if configured
                    approve_role = app_settings.get('approve_role')
                    if approve_role:
                        guild = _bot.get_guild(int(guild_id))
                        if guild:
                            member = guild.get_member(int(sub['user_id']))
                            role = guild.get_role(int(approve_role))
                            if member and role:
                                asyncio.run_coroutine_threadsafe(member.add_roles(role, reason="Application approved"), _bot.loop)
                else:
                    embed = discord.Embed(
                        title=f"Application Denied",
                        description=f"Your application for **{form['name']}** has been denied.",
                        color=discord.Color.red(),
                    )
                    if reason:
                        embed.add_field(name="Reason", value=reason, inline=False)

                embed.set_footer(text="Apex")
                asyncio.run_coroutine_threadsafe(user.send(embed=embed), _bot.loop)
        except:
            pass

    return jsonify({'success': True})


def run_api(port=5050):
    app.run(host='0.0.0.0', port=port, threaded=True)


if __name__ == '__main__':
    run_api()
