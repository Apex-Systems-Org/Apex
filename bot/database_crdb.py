import psycopg2
import psycopg2.extras
import psycopg2.pool
import json
import os
from datetime import datetime
import uuid


class Database:
    def __init__(self):
        dsn = os.getenv("COCKROACH_DSN")
        if not dsn:
            raise ValueError("COCKROACH_DSN environment variable is not set")
        self.pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            dsn=dsn
        )
        self._initialize()

    def _get_connection(self):
        conn = self.pool.getconn()
        return conn

    def _return_connection(self, conn):
        self.pool.putconn(conn)

    def _initialize(self):
        conn = self._get_connection()
        cursor = conn.cursor()

        # Guild settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id TEXT PRIMARY KEY,
                settings TEXT DEFAULT '{}'
            )
        ''')

        # Mod logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mod_logs (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                action TEXT,
                user_id TEXT,
                moderator_id TEXT,
                reason TEXT,
                timestamp TEXT,
                case_num INTEGER,
                duration TEXT,
                extra TEXT DEFAULT '{}'
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mod_logs_guild ON mod_logs(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mod_logs_case ON mod_logs(guild_id, case_num)')

        # Warnings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                moderator_id TEXT,
                reason TEXT,
                timestamp TEXT,
                case_num INTEGER
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings(guild_id, user_id)')

        # Ticket panels
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticket_panels (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                data TEXT DEFAULT '{}'
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ticket_panels_guild ON ticket_panels(guild_id)')

        # Tickets
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                channel_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                user_id TEXT,
                panel_id TEXT,
                ticket_number INTEGER,
                ticket_type TEXT,
                status TEXT DEFAULT 'open',
                claimed_by TEXT,
                created_at TEXT,
                closed_at TEXT,
                extra TEXT DEFAULT '{}'
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tickets_guild ON tickets(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(guild_id, user_id, status)')

        # Transcripts
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transcripts (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                ticket_number INTEGER,
                ticket_type TEXT,
                user_id TEXT,
                channel_name TEXT,
                messages TEXT,
                created_at TEXT,
                extra TEXT DEFAULT '{}'
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_transcripts_guild ON transcripts(guild_id)')

        # Bot guilds
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_guilds (
                guild_id TEXT PRIMARY KEY,
                name TEXT,
                joined_at TEXT
            )
        ''')

        # Custom commands
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS custom_commands (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                response TEXT,
                created_by TEXT,
                created_at TEXT,
                extra TEXT DEFAULT '{}'
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_custom_commands_guild ON custom_commands(guild_id)')
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_commands_name ON custom_commands(guild_id, name)')

        # Reaction role panels
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reaction_role_panels (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                message_id TEXT,
                channel_id TEXT,
                data TEXT DEFAULT '{}'
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rr_panels_guild ON reaction_role_panels(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rr_panels_message ON reaction_role_panels(message_id)')

        # Blacklist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id TEXT PRIMARY KEY,
                reason TEXT,
                added_by TEXT,
                added_at TEXT
            )
        ''')

        # Knowledge base / FAQ
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id TEXT PRIMARY KEY,
                title TEXT,
                content TEXT,
                category TEXT,
                tags TEXT,
                created_by TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''')

        # Bot status
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_status (
                id TEXT PRIMARY KEY DEFAULT 'current',
                data TEXT DEFAULT '{}'
            )
        ''')

        # Error logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS error_logs (
                id TEXT PRIMARY KEY,
                error TEXT,
                command TEXT,
                guild_id TEXT,
                user_id TEXT,
                timestamp TEXT
            )
        ''')

        # Support templates
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS support_templates (
                id TEXT PRIMARY KEY,
                title TEXT,
                content TEXT,
                category TEXT,
                created_by TEXT,
                created_at TEXT
            )
        ''')

        # User notes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_notes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                note TEXT,
                added_by TEXT,
                created_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_notes_user ON user_notes(user_id)')

        # Audit logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                action TEXT,
                staff_id TEXT,
                target_id TEXT,
                target_type TEXT,
                details TEXT,
                guild_id TEXT,
                timestamp TEXT
            )
        ''')

        # Server health
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS server_health (
                guild_id TEXT PRIMARY KEY,
                guild_name TEXT,
                issues_count INTEGER DEFAULT 0,
                issues TEXT DEFAULT '[]',
                last_checked TEXT
            )
        ''')

        # Suggestions
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS suggestions (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                message_id TEXT,
                channel_id TEXT,
                content TEXT,
                status TEXT DEFAULT 'pending',
                upvotes INTEGER DEFAULT 0,
                downvotes INTEGER DEFAULT 0,
                voters TEXT DEFAULT '[]',
                staff_response TEXT,
                responded_by TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_suggestions_guild ON suggestions(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_suggestions_user ON suggestions(guild_id, user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_suggestions_message ON suggestions(message_id)')

        # Auto-responders
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auto_responders (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                trigger_word TEXT NOT NULL,
                response TEXT,
                match_type TEXT DEFAULT 'contains',
                ignore_case INTEGER DEFAULT 1,
                enabled INTEGER DEFAULT 1,
                created_by TEXT,
                created_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_auto_responders_guild ON auto_responders(guild_id)')

        # Giveaways
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS giveaways (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT,
                prize TEXT,
                winners_count INTEGER DEFAULT 1,
                host_id TEXT,
                required_role TEXT,
                ends_at TEXT,
                ended INTEGER DEFAULT 0,
                created_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_giveaways_guild ON giveaways(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_giveaways_message ON giveaways(message_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_giveaways_active ON giveaways(ended, ends_at)')

        # Giveaway entries
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS giveaway_entries (
                giveaway_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                entered_at TEXT,
                PRIMARY KEY (giveaway_id, user_id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_giveaway_entries ON giveaway_entries(giveaway_id)')

        # AFK
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS afk (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                reason TEXT,
                set_at TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_afk_guild ON afk(guild_id)')

        # Economy
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS economy (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                balance INTEGER DEFAULT 0,
                bank INTEGER DEFAULT 0,
                last_daily TEXT,
                last_work TEXT,
                total_earned INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_economy_guild ON economy(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_economy_balance ON economy(guild_id, balance DESC)')

        # Invite tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS invite_tracking (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                inviter_id TEXT,
                invite_code TEXT,
                joined_at TEXT,
                "left" INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_invite_tracking_guild ON invite_tracking(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_invite_tracking_inviter ON invite_tracking(guild_id, inviter_id)')

        # Invite cache (for tracking invite uses)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS invite_cache (
                guild_id TEXT NOT NULL,
                code TEXT NOT NULL,
                uses INTEGER DEFAULT 0,
                inviter_id TEXT,
                PRIMARY KEY (guild_id, code)
            )
        ''')

        # Leveling system
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS leveling (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                total_messages INTEGER DEFAULT 0,
                last_xp_time TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_leveling_guild ON leveling(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_leveling_xp ON leveling(guild_id, xp DESC)')

        # Level roles (roles given at certain levels)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS level_roles (
                guild_id TEXT NOT NULL,
                level INTEGER NOT NULL,
                role_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, level)
            )
        ''')

        # Voice channel generators (Join to Create)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS voice_generators (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                category_id TEXT,
                name_template TEXT DEFAULT '{user}''s Channel',
                user_limit INTEGER DEFAULT 0,
                bitrate INTEGER DEFAULT 64000,
                created_by TEXT,
                created_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_voice_generators_guild ON voice_generators(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_voice_generators_channel ON voice_generators(channel_id)')

        # Temporary voice channels (created by users)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS temp_voice_channels (
                channel_id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                generator_id TEXT,
                created_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_temp_vc_guild ON temp_voice_channels(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_temp_vc_owner ON temp_voice_channels(owner_id)')

        # Dev auth codes (for developer dashboard server access)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dev_auth_codes (
                code TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at TEXT,
                expires_at TEXT,
                max_uses INTEGER DEFAULT 1,
                use_count INTEGER DEFAULT 0,
                used_by TEXT,
                used_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_dev_auth_codes_guild ON dev_auth_codes(guild_id)')

        # Incidents (for status page)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'investigating',
                severity TEXT DEFAULT 'minor',
                affected_services TEXT DEFAULT '[]',
                created_by TEXT,
                created_at TEXT,
                updated_at TEXT,
                resolved_at TEXT,
                updates TEXT DEFAULT '[]'
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at)')

        # Tempbans
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tempbans (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                moderator_id TEXT,
                reason TEXT,
                banned_at TEXT,
                expires_at TEXT NOT NULL
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tempbans_expires ON tempbans(expires_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_tempbans_guild ON tempbans(guild_id)')

        # Starboard posts
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS starboard_posts (
                guild_id TEXT NOT NULL,
                original_message_id TEXT NOT NULL,
                starboard_message_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, original_message_id)
            )
        ''')

        # Ticket feedback
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticket_feedback (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                ticket_number INTEGER,
                user_id TEXT,
                rating INTEGER,
                created_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ticket_feedback_guild ON ticket_feedback(guild_id)')

        # LOA
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS loa_requests (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                reason TEXT,
                start_date TEXT,
                end_date TEXT,
                status TEXT DEFAULT 'pending',
                approved_by TEXT,
                denied_by TEXT,
                created_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_loa_guild ON loa_requests(guild_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_loa_user ON loa_requests(guild_id, user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_loa_status ON loa_requests(guild_id, status)')

        # Snippets
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS snippets (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT,
                created_by TEXT,
                created_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_snippets_guild ON snippets(guild_id)')
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_snippets_name ON snippets(guild_id, name)')

        # Ticket bans
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticket_bans (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                banned_by TEXT,
                reason TEXT,
                created_at TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')

        conn.commit()
        self._return_connection(conn)
        print("CockroachDB database initialized")

    def _generate_id(self) -> str:
        return str(uuid.uuid4())

    def _now(self) -> str:
        return datetime.utcnow().isoformat()

    # Guild settings
    def get_guild_settings(self, guild_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT settings FROM guilds WHERE guild_id = %s', (str(guild_id),))
        row = cursor.fetchone()
        self._return_connection(conn)
        return json.loads(row['settings']) if row else {}

    def update_guild_settings(self, guild_id: str, settings: dict):
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT settings FROM guilds WHERE guild_id = %s', (str(guild_id),))
        row = cursor.fetchone()
        existing = json.loads(row['settings']) if row else {}
        existing.update(settings)
        cursor.execute('''
            INSERT INTO guilds (guild_id, settings) VALUES (%s, %s)
            ON CONFLICT(guild_id) DO UPDATE SET settings = %s
        ''', (str(guild_id), json.dumps(existing), json.dumps(existing)))
        conn.commit()
        self._return_connection(conn)

    # Moderation logs
    def log_action(self, guild_id: str, action: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        log_id = self._generate_id()
        extra = {k: v for k, v in action.items() if k not in ['action', 'user_id', 'moderator_id', 'reason', 'timestamp', 'case', 'duration']}
        cursor.execute('''
            INSERT INTO mod_logs (id, guild_id, action, user_id, moderator_id, reason, timestamp, case_num, duration, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            log_id, str(guild_id), action.get('action'), action.get('user_id'),
            action.get('moderator_id'), action.get('reason'), action.get('timestamp', self._now()),
            action.get('case'), action.get('duration'), json.dumps(extra)
        ))
        conn.commit()
        self._return_connection(conn)

    def get_mod_logs(self, guild_id: str, limit: int = 50) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('''
            SELECT * FROM mod_logs WHERE guild_id = %s
            ORDER BY timestamp DESC LIMIT %s
        ''', (str(guild_id), limit))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            data['case'] = data.pop('case_num')
            extra = json.loads(data.pop('extra', '{}'))
            data.update(extra)
            del data['id']
            del data['guild_id']
            result.append(data)
        return result

    def get_case(self, guild_id: str, case_num: int) -> dict | None:
        """Get a specific case by number."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM mod_logs WHERE guild_id = %s AND case_num = %s', (str(guild_id), case_num))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        data['case'] = data.pop('case_num')
        extra = json.loads(data.pop('extra', '{}'))
        data.update(extra)
        del data['id']
        del data['guild_id']
        return data

    def get_highest_case_number(self, guild_id: str) -> int:
        """Get the highest case number in mod logs for a guild."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT MAX(case_num) as max FROM mod_logs WHERE guild_id = %s', (str(guild_id),))
        result = cursor.fetchone()
        self._return_connection(conn)
        return result['max'] or 0 if result else 0

    # Warnings
    def add_warning(self, guild_id: str, user_id: str, warning: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        warning_id = self._generate_id()
        cursor.execute('''
            INSERT INTO warnings (id, guild_id, user_id, moderator_id, reason, timestamp, case_num)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (
            warning_id, str(guild_id), str(user_id), warning.get('moderator_id'),
            warning.get('reason'), warning.get('timestamp', self._now()), warning.get('case')
        ))
        conn.commit()
        self._return_connection(conn)

    def get_user_warnings(self, guild_id: str, user_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM warnings WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            data['case'] = data.pop('case_num')
            del data['guild_id']
            result.append(data)
        return result

    def delete_warning(self, guild_id: str, warning_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM warnings WHERE id = %s AND guild_id = %s', (warning_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    def clear_warnings(self, guild_id: str, user_id: str) -> int:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT COUNT(*) as count FROM warnings WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        count = cursor.fetchone()['count']
        cursor.execute('DELETE FROM warnings WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)
        return count

    def get_warnings(self, guild_id: str, user_id: str) -> list:
        return self.get_user_warnings(guild_id, user_id)

    def get_all_warnings(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM warnings WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            data['case'] = data.pop('case_num')
            del data['guild_id']
            result.append(data)
        return result

    # Prefix
    def get_prefix(self, guild_id: str) -> str:
        settings = self.get_guild_settings(guild_id)
        return settings.get("prefix", "a!")

    def set_prefix(self, guild_id: str, prefix: str):
        self.update_guild_settings(guild_id, {"prefix": prefix})

    # Case number
    def get_next_case_number(self, guild_id: str) -> int:
        settings = self.get_guild_settings(guild_id)
        case_num = settings.get("case_number", 0) + 1
        self.update_guild_settings(guild_id, {"case_number": case_num})
        return case_num

    # Void case
    def void_case(self, guild_id: str, case_num: int) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM mod_logs WHERE guild_id = %s AND case_num = %s', (str(guild_id), case_num))
        deleted_logs = cursor.rowcount
        cursor.execute('DELETE FROM warnings WHERE guild_id = %s AND case_num = %s', (str(guild_id), case_num))
        deleted_warnings = cursor.rowcount
        conn.commit()
        self._return_connection(conn)
        return deleted_logs > 0 or deleted_warnings > 0

    def clear_all_mod_logs(self, guild_id: str) -> int:
        """Delete all mod logs for a guild. Returns number of deleted records."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM mod_logs WHERE guild_id = %s', (str(guild_id),))
        deleted = cursor.rowcount
        conn.commit()
        self._return_connection(conn)
        # Reset case counter in guild settings
        self.update_guild_settings(guild_id, {"case_number": 0})
        return deleted

    def update_case_reason(self, guild_id: str, case_num: int, new_reason: str) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE mod_logs SET reason = %s WHERE guild_id = %s AND case_num = %s',
                      (new_reason, str(guild_id), case_num))
        updated = cursor.rowcount > 0
        conn.commit()
        self._return_connection(conn)
        return updated

    def get_mod_stats(self, guild_id: str, moderator_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT action FROM mod_logs WHERE guild_id = %s AND moderator_id = %s',
                      (str(guild_id), str(moderator_id)))
        rows = cursor.fetchall()
        self._return_connection(conn)
        stats = {'total': 0, 'warn': 0, 'kick': 0, 'ban': 0, 'timeout': 0, 'mute': 0, 'softban': 0, 'unban': 0}
        for row in rows:
            action = (row['action'] or '').lower()
            stats['total'] += 1
            if action in stats:
                stats[action] += 1
        return stats

    def get_mod_leaderboard(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('''
            SELECT moderator_id, COUNT(*) as count FROM mod_logs
            WHERE guild_id = %s GROUP BY moderator_id ORDER BY count DESC
        ''', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [(row['moderator_id'], row['count']) for row in rows]

    def transfer_mod_logs(self, guild_id: str, from_user_id: str, to_user_id: str) -> int:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE mod_logs SET moderator_id = %s WHERE guild_id = %s AND moderator_id = %s',
                      (str(to_user_id), str(guild_id), str(from_user_id)))
        count = cursor.rowcount
        conn.commit()
        self._return_connection(conn)
        return count

    # Ticket Panels
    def create_ticket_panel(self, guild_id: str, panel: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        panel_id = panel.get('id') or self._generate_id()
        panel['id'] = panel_id
        cursor.execute('''
            INSERT INTO ticket_panels (id, guild_id, data) VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET guild_id = %s, data = %s
        ''', (panel_id, str(guild_id), json.dumps(panel), str(guild_id), json.dumps(panel)))
        conn.commit()
        self._return_connection(conn)
        return panel_id

    def get_ticket_panel(self, guild_id: str, panel_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT data FROM ticket_panels WHERE id = %s AND guild_id = %s', (panel_id, str(guild_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        return json.loads(row['data']) if row else None

    def get_all_ticket_panels(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT data FROM ticket_panels WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [json.loads(row['data']) for row in rows]

    def update_ticket_panel(self, guild_id: str, panel_id: str, updates: dict):
        panel = self.get_ticket_panel(guild_id, panel_id)
        if panel:
            panel.update(updates)
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE ticket_panels SET data = %s WHERE id = %s AND guild_id = %s',
                          (json.dumps(panel), panel_id, str(guild_id)))
            conn.commit()
            self._return_connection(conn)

    def delete_ticket_panel(self, guild_id: str, panel_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM ticket_panels WHERE id = %s AND guild_id = %s', (panel_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    # Tickets
    def get_next_ticket_number(self, guild_id: str) -> int:
        settings = self.get_guild_settings(guild_id)
        ticket_num = settings.get("ticket_number", 0) + 1
        self.update_guild_settings(guild_id, {"ticket_number": ticket_num})
        return ticket_num

    def create_ticket(self, guild_id: str, ticket: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        ticket_type = ticket.get('ticket_type')
        if isinstance(ticket_type, dict):
            ticket_type = json.dumps(ticket_type)
        extra = {k: v for k, v in ticket.items() if k not in ['channel_id', 'guild_id', 'user_id', 'panel_id', 'ticket_number', 'ticket_type', 'status', 'claimed_by', 'created_at', 'closed_at']}
        cursor.execute('''
            INSERT INTO tickets (channel_id, guild_id, user_id, panel_id, ticket_number, ticket_type, status, claimed_by, created_at, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (channel_id) DO UPDATE SET
                guild_id = %s, user_id = %s, panel_id = %s, ticket_number = %s,
                ticket_type = %s, status = %s, claimed_by = %s, created_at = %s, extra = %s
        ''', (
            str(ticket['channel_id']), str(guild_id), ticket.get('user_id'), ticket.get('panel_id'),
            ticket.get('ticket_number'), ticket_type, ticket.get('status', 'open'),
            ticket.get('claimed_by'), ticket.get('created_at', self._now()), json.dumps(extra),
            str(guild_id), ticket.get('user_id'), ticket.get('panel_id'),
            ticket.get('ticket_number'), ticket_type, ticket.get('status', 'open'),
            ticket.get('claimed_by'), ticket.get('created_at', self._now()), json.dumps(extra)
        ))
        conn.commit()
        self._return_connection(conn)

    def get_ticket(self, guild_id: str, channel_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM tickets WHERE channel_id = %s AND guild_id = %s', (str(channel_id), str(guild_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        extra = json.loads(data.pop('extra', '{}'))
        data.update(extra)
        if data.get('ticket_type') and isinstance(data['ticket_type'], str) and data['ticket_type'].startswith('{'):
            try:
                data['ticket_type'] = json.loads(data['ticket_type'])
            except:
                pass
        return data

    def get_user_tickets(self, guild_id: str, user_id: str, panel_id: str = None) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if panel_id:
            cursor.execute('SELECT * FROM tickets WHERE guild_id = %s AND user_id = %s AND status = %s AND panel_id = %s',
                          (str(guild_id), str(user_id), 'open', panel_id))
        else:
            cursor.execute('SELECT * FROM tickets WHERE guild_id = %s AND user_id = %s AND status = %s',
                          (str(guild_id), str(user_id), 'open'))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            extra = json.loads(data.pop('extra', '{}'))
            data.update(extra)
            result.append(data)
        return result

    def get_user_ticket_counts(self, guild_id: str, user_id: str) -> dict:
        """Get open and total ticket counts for a user in a guild."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT COUNT(*) as count FROM tickets WHERE guild_id = %s AND user_id = %s AND status = %s',
                      (str(guild_id), str(user_id), 'open'))
        open_count = cursor.fetchone()['count']
        cursor.execute('SELECT COUNT(*) as count FROM tickets WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        total_count = cursor.fetchone()['count']
        self._return_connection(conn)
        return {'open': open_count, 'total': total_count}

    def update_ticket(self, guild_id: str, channel_id: str, updates: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        for key, value in updates.items():
            if key in ['status', 'claimed_by', 'closed_at']:
                cursor.execute(f'UPDATE tickets SET {key} = %s WHERE channel_id = %s AND guild_id = %s',
                              (value, str(channel_id), str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    def close_ticket(self, guild_id: str, channel_id: str):
        self.update_ticket(guild_id, channel_id, {'status': 'closed', 'closed_at': self._now()})

    def delete_ticket(self, guild_id: str, channel_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM tickets WHERE channel_id = %s AND guild_id = %s', (str(channel_id), str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    def get_all_tickets(self, guild_id: str, status: str = None) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if status:
            cursor.execute('SELECT * FROM tickets WHERE guild_id = %s AND status = %s', (str(guild_id), status))
        else:
            cursor.execute('SELECT * FROM tickets WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            extra = json.loads(data.pop('extra', '{}'))
            data.update(extra)
            result.append(data)
        return result

    # Transcripts
    def save_transcript(self, guild_id: str, transcript: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        transcript_id = transcript.get('id') or self._generate_id()
        messages = transcript.get('messages', [])
        if isinstance(messages, list):
            messages = json.dumps(messages)
        extra = {k: v for k, v in transcript.items() if k not in ['id', 'guild_id', 'ticket_number', 'ticket_type', 'user_id', 'channel_name', 'messages', 'created_at']}
        cursor.execute('''
            INSERT INTO transcripts (id, guild_id, ticket_number, ticket_type, user_id, channel_name, messages, created_at, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                guild_id = %s, ticket_number = %s, ticket_type = %s, user_id = %s,
                channel_name = %s, messages = %s, created_at = %s, extra = %s
        ''', (
            transcript_id, str(guild_id), transcript.get('ticket_number'), transcript.get('ticket_type'),
            transcript.get('user_id'), transcript.get('channel_name'), messages,
            transcript.get('created_at', self._now()), json.dumps(extra),
            str(guild_id), transcript.get('ticket_number'), transcript.get('ticket_type'),
            transcript.get('user_id'), transcript.get('channel_name'), messages,
            transcript.get('created_at', self._now()), json.dumps(extra)
        ))
        conn.commit()
        self._return_connection(conn)
        return transcript_id

    def get_transcript(self, guild_id: str, transcript_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM transcripts WHERE id = %s AND guild_id = %s', (transcript_id, str(guild_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        data['messages'] = json.loads(data['messages']) if data['messages'] else []
        extra = json.loads(data.pop('extra', '{}'))
        data.update(extra)
        return data

    def get_all_transcripts(self, guild_id: str, limit: int = 50) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM transcripts WHERE guild_id = %s ORDER BY created_at DESC LIMIT %s', (str(guild_id), limit))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            data['messages'] = json.loads(data['messages']) if data['messages'] else []
            extra = json.loads(data.pop('extra', '{}'))
            data.update(extra)
            result.append(data)
        return result

    # Bot guilds
    def add_bot_guild(self, guild_id: str, guild_name: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO bot_guilds (guild_id, name, joined_at) VALUES (%s, %s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET name = %s, joined_at = %s
        ''', (str(guild_id), guild_name, self._now(), guild_name, self._now()))
        conn.commit()
        self._return_connection(conn)

    def remove_bot_guild(self, guild_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM bot_guilds WHERE guild_id = %s', (str(guild_id),))
        conn.commit()
        self._return_connection(conn)

    def get_all_bot_guilds(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM bot_guilds')
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def sync_bot_guilds(self, guilds: list):
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT guild_id FROM bot_guilds')
        existing = {row['guild_id'] for row in cursor.fetchall()}
        current = {str(g.id) for g in guilds}

        for guild_id in existing - current:
            cursor.execute('DELETE FROM bot_guilds WHERE guild_id = %s', (guild_id,))

        for guild in guilds:
            cursor.execute('''
                INSERT INTO bot_guilds (guild_id, name, joined_at) VALUES (%s, %s, %s)
                ON CONFLICT (guild_id) DO UPDATE SET name = %s, joined_at = %s
            ''', (str(guild.id), guild.name, self._now(), guild.name, self._now()))

        conn.commit()
        self._return_connection(conn)

    # Bot status tracking
    def update_bot_status(self, status: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO bot_status (id, data) VALUES ('current', %s)
            ON CONFLICT (id) DO UPDATE SET data = %s
        ''', (json.dumps(status), json.dumps(status)))
        conn.commit()
        self._return_connection(conn)

    def get_bot_status(self) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT data FROM bot_status WHERE id = %s', ('current',))
        row = cursor.fetchone()
        self._return_connection(conn)
        return json.loads(row['data']) if row else {}

    def log_error(self, error: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        # Generate a short readable error ID like "error_AW2L4LGK2G"
        import random
        import string
        short_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        error_id = f"error_{short_id}"
        cursor.execute('''
            INSERT INTO error_logs (id, error, command, guild_id, user_id, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (error_id, error.get('error'), error.get('command'), error.get('guild_id'),
              error.get('user_id'), self._now()))
        conn.commit()
        self._return_connection(conn)
        return error_id

    def get_error_log(self, error_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM error_logs WHERE id = %s', (error_id,))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    # Custom Commands
    def create_custom_command(self, guild_id: str, command: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        cmd_id = f"{guild_id}_{command['name'].lower()}"
        extra = {k: v for k, v in command.items() if k not in ['name', 'response', 'created_by', 'created_at']}
        cursor.execute('''
            INSERT INTO custom_commands (id, guild_id, name, response, created_by, created_at, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                guild_id = %s, name = %s, response = %s, created_by = %s, created_at = %s, extra = %s
        ''', (cmd_id, str(guild_id), command['name'].lower(), command.get('response'),
              command.get('created_by'), self._now(), json.dumps(extra),
              str(guild_id), command['name'].lower(), command.get('response'),
              command.get('created_by'), self._now(), json.dumps(extra)))
        conn.commit()
        self._return_connection(conn)
        return cmd_id

    def get_custom_command(self, guild_id: str, name: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM custom_commands WHERE guild_id = %s AND name = %s', (str(guild_id), name.lower()))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        extra = json.loads(data.pop('extra', '{}'))
        data.update(extra)
        return data

    def get_all_custom_commands(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM custom_commands WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            extra = json.loads(data.pop('extra', '{}'))
            data.update(extra)
            result.append(data)
        return result

    def update_custom_command(self, guild_id: str, name: str, updates: dict):
        cmd = self.get_custom_command(guild_id, name)
        if cmd:
            cmd.update(updates)
            self.create_custom_command(guild_id, cmd)

    def delete_custom_command(self, guild_id: str, name: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM custom_commands WHERE guild_id = %s AND name = %s', (str(guild_id), name.lower()))
        conn.commit()
        self._return_connection(conn)

    # Reaction Role Panels
    def create_reaction_role_panel(self, guild_id: str, panel: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        panel_id = panel.get('id') or self._generate_id()
        panel['id'] = panel_id
        cursor.execute('''
            INSERT INTO reaction_role_panels (id, guild_id, message_id, channel_id, data)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                guild_id = %s, message_id = %s, channel_id = %s, data = %s
        ''', (panel_id, str(guild_id), panel.get('message_id'), panel.get('channel_id'), json.dumps(panel),
              str(guild_id), panel.get('message_id'), panel.get('channel_id'), json.dumps(panel)))
        conn.commit()
        self._return_connection(conn)
        return panel_id

    def get_reaction_role_panel(self, guild_id: str, panel_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT data FROM reaction_role_panels WHERE id = %s AND guild_id = %s', (panel_id, str(guild_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = json.loads(row['data'])
        data['id'] = panel_id
        return data

    def get_all_reaction_role_panels(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT id, data FROM reaction_role_panels WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = json.loads(row['data'])
            data['id'] = row['id']
            result.append(data)
        return result

    def update_reaction_role_panel(self, guild_id: str, panel_id: str, updates: dict):
        panel = self.get_reaction_role_panel(guild_id, panel_id)
        if panel:
            panel.update(updates)
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE reaction_role_panels SET data = %s, message_id = %s, channel_id = %s
                WHERE id = %s AND guild_id = %s
            ''', (json.dumps(panel), panel.get('message_id'), panel.get('channel_id'), panel_id, str(guild_id)))
            conn.commit()
            self._return_connection(conn)

    def delete_reaction_role_panel(self, guild_id: str, panel_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM reaction_role_panels WHERE id = %s AND guild_id = %s', (panel_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    def get_reaction_role_panel_by_message(self, guild_id: str, message_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT id, data FROM reaction_role_panels WHERE guild_id = %s AND message_id = %s',
                      (str(guild_id), str(message_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = json.loads(row['data'])
        data['id'] = row['id']
        return data

    # Blacklist
    def is_blacklisted(self, user_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM blacklist WHERE user_id = %s', (str(user_id),))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def add_to_blacklist(self, user_id: str, reason: str, added_by: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO blacklist (user_id, reason, added_by, added_at) VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET reason = %s, added_by = %s, added_at = %s
        ''', (str(user_id), reason, str(added_by), self._now(), reason, str(added_by), self._now()))
        conn.commit()
        self._return_connection(conn)

    def remove_from_blacklist(self, user_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM blacklist WHERE user_id = %s', (str(user_id),))
        conn.commit()
        self._return_connection(conn)

    def get_blacklist(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM blacklist')
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    # Knowledge Base / FAQ
    def create_faq(self, faq: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        faq_id = faq.get('id') or self._generate_id()
        tags = faq.get('tags', [])
        if isinstance(tags, list):
            tags = json.dumps(tags)
        cursor.execute('''
            INSERT INTO knowledge_base (id, title, content, category, tags, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                title = %s, content = %s, category = %s, tags = %s, created_by = %s, created_at = %s
        ''', (faq_id, faq.get('title'), faq.get('content'), faq.get('category'),
              tags, faq.get('created_by'), self._now(),
              faq.get('title'), faq.get('content'), faq.get('category'),
              tags, faq.get('created_by'), self._now()))
        conn.commit()
        self._return_connection(conn)
        return faq_id

    def get_faq(self, faq_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM knowledge_base WHERE id = %s', (faq_id,))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        data['tags'] = json.loads(data['tags']) if data['tags'] else []
        return data

    def get_all_faqs(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM knowledge_base ORDER BY created_at DESC')
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            data['tags'] = json.loads(data['tags']) if data['tags'] else []
            result.append(data)
        return result

    def update_faq(self, faq_id: str, updates: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        updates['updated_at'] = self._now()
        for key, value in updates.items():
            if key == 'tags' and isinstance(value, list):
                value = json.dumps(value)
            cursor.execute(f'UPDATE knowledge_base SET {key} = %s WHERE id = %s', (value, faq_id))
        conn.commit()
        self._return_connection(conn)

    def delete_faq(self, faq_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM knowledge_base WHERE id = %s', (faq_id,))
        conn.commit()
        self._return_connection(conn)

    # Support Templates
    def create_template(self, template: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        template_id = template.get('id') or self._generate_id()
        cursor.execute('''
            INSERT INTO support_templates (id, title, content, category, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                title = %s, content = %s, category = %s, created_by = %s, created_at = %s
        ''', (template_id, template.get('title'), template.get('content'),
              template.get('category'), template.get('created_by'), self._now(),
              template.get('title'), template.get('content'),
              template.get('category'), template.get('created_by'), self._now()))
        conn.commit()
        self._return_connection(conn)
        return template_id

    def get_all_templates(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM support_templates ORDER BY category, title')
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def update_template(self, template_id: str, updates: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        for key, value in updates.items():
            cursor.execute(f'UPDATE support_templates SET {key} = %s WHERE id = %s', (value, template_id))
        conn.commit()
        self._return_connection(conn)

    def delete_template(self, template_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM support_templates WHERE id = %s', (template_id,))
        conn.commit()
        self._return_connection(conn)

    # User Notes
    def add_user_note(self, user_id: str, note: str, added_by: str) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        note_id = self._generate_id()
        cursor.execute('''
            INSERT INTO user_notes (id, user_id, note, added_by, created_at)
            VALUES (%s, %s, %s, %s, %s)
        ''', (note_id, str(user_id), note, str(added_by), self._now()))
        conn.commit()
        self._return_connection(conn)
        return note_id

    def get_user_notes(self, user_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM user_notes WHERE user_id = %s ORDER BY created_at DESC', (str(user_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def delete_user_note(self, note_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM user_notes WHERE id = %s', (note_id,))
        conn.commit()
        self._return_connection(conn)

    # Audit Logs
    def add_audit_log(self, log: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        log_id = self._generate_id()
        cursor.execute('''
            INSERT INTO audit_logs (id, action, staff_id, target_id, target_type, details, guild_id, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ''', (log_id, log.get('action'), log.get('staff_id'), log.get('target_id'),
              log.get('target_type'), log.get('details'), log.get('guild_id'), self._now()))
        conn.commit()
        self._return_connection(conn)
        return log_id

    def get_audit_logs(self, staff_id: str = None, limit: int = 50) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if staff_id:
            cursor.execute('SELECT * FROM audit_logs WHERE staff_id = %s ORDER BY timestamp DESC LIMIT %s',
                          (str(staff_id), limit))
        else:
            cursor.execute('SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT %s', (limit,))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    # Server Health
    def update_server_health(self, guild_id: str, guild_name: str, issues: list):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO server_health (guild_id, guild_name, issues_count, issues, last_checked)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET
                guild_name = %s, issues_count = %s, issues = %s, last_checked = %s
        ''', (str(guild_id), guild_name, len(issues), json.dumps(issues), self._now(),
              guild_name, len(issues), json.dumps(issues), self._now()))
        conn.commit()
        self._return_connection(conn)

    def get_all_server_health(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM server_health ORDER BY issues_count DESC')
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            data['issues'] = json.loads(data['issues']) if data['issues'] else []
            result.append(data)
        return result

    # Error logs
    def get_error_logs(self, limit: int = 50) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM error_logs ORDER BY timestamp DESC LIMIT %s', (limit,))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def delete_error_log(self, log_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM error_logs WHERE id = %s', (log_id,))
        conn.commit()
        self._return_connection(conn)

    # Suggestions
    def create_suggestion(self, guild_id: str, suggestion: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        suggestion_id = suggestion.get('id') or self._generate_id()
        voters = suggestion.get('voters', [])
        if isinstance(voters, list):
            voters = json.dumps(voters)
        cursor.execute('''
            INSERT INTO suggestions (id, guild_id, user_id, message_id, channel_id, content, status, upvotes, downvotes, voters, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            suggestion_id, str(guild_id), suggestion.get('user_id'), suggestion.get('message_id'),
            suggestion.get('channel_id'), suggestion.get('content'), suggestion.get('status', 'pending'),
            suggestion.get('upvotes', 0), suggestion.get('downvotes', 0), voters, self._now()
        ))
        conn.commit()
        self._return_connection(conn)
        return suggestion_id

    def get_suggestion(self, guild_id: str, suggestion_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM suggestions WHERE id = %s AND guild_id = %s', (suggestion_id, str(guild_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        data['voters'] = json.loads(data['voters']) if data['voters'] else []
        return data

    def get_suggestion_by_message(self, message_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM suggestions WHERE message_id = %s', (str(message_id),))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        data['voters'] = json.loads(data['voters']) if data['voters'] else []
        return data

    def get_suggestions(self, guild_id: str, status: str = None, limit: int = 50) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if status:
            cursor.execute('SELECT * FROM suggestions WHERE guild_id = %s AND status = %s ORDER BY created_at DESC LIMIT %s',
                          (str(guild_id), status, limit))
        else:
            cursor.execute('SELECT * FROM suggestions WHERE guild_id = %s ORDER BY created_at DESC LIMIT %s',
                          (str(guild_id), limit))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            data['voters'] = json.loads(data['voters']) if data['voters'] else []
            result.append(data)
        return result

    def update_suggestion(self, guild_id: str, suggestion_id: str, updates: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        updates['updated_at'] = self._now()
        for key, value in updates.items():
            if key == 'voters' and isinstance(value, list):
                value = json.dumps(value)
            if key in ['status', 'upvotes', 'downvotes', 'voters', 'staff_response', 'responded_by', 'updated_at']:
                cursor.execute(f'UPDATE suggestions SET {key} = %s WHERE id = %s AND guild_id = %s',
                              (value, suggestion_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    def vote_suggestion(self, suggestion_id: str, user_id: str, vote_type: str) -> str:
        """Vote on a suggestion. vote_type is 'up' or 'down'.
        Returns: 'added', 'removed', 'changed', or 'error'."""
        suggestion = self.get_suggestion_by_message(suggestion_id)
        if not suggestion:
            return 'error'
        voters = suggestion.get('voters', [])
        upvotes = suggestion['upvotes']
        downvotes = suggestion['downvotes']

        # Check if user already voted
        existing_vote = None
        for v in voters:
            if v.get('user_id') == str(user_id):
                existing_vote = v
                break

        if existing_vote:
            if existing_vote['vote'] == vote_type:
                # Same vote - remove it
                voters.remove(existing_vote)
                if vote_type == 'up':
                    upvotes -= 1
                else:
                    downvotes -= 1
                result = 'removed'
            else:
                # Different vote - change it
                existing_vote['vote'] = vote_type
                if vote_type == 'up':
                    upvotes += 1
                    downvotes -= 1
                else:
                    upvotes -= 1
                    downvotes += 1
                result = 'changed'
        else:
            # New vote
            voters.append({'user_id': str(user_id), 'vote': vote_type})
            if vote_type == 'up':
                upvotes += 1
            else:
                downvotes += 1
            result = 'added'

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE suggestions SET upvotes = %s, downvotes = %s, voters = %s, updated_at = %s WHERE id = %s',
                      (upvotes, downvotes, json.dumps(voters), self._now(), suggestion['id']))
        conn.commit()
        self._return_connection(conn)
        return result

    def delete_suggestion(self, guild_id: str, suggestion_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM suggestions WHERE id = %s AND guild_id = %s', (suggestion_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    # Auto-responders
    def create_auto_responder(self, guild_id: str, responder: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        responder_id = responder.get('id') or self._generate_id()
        cursor.execute('''
            INSERT INTO auto_responders (id, guild_id, trigger_word, response, match_type, ignore_case, enabled, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            responder_id, str(guild_id), responder.get('trigger_word'), responder.get('response'),
            responder.get('match_type', 'contains'), 1 if responder.get('ignore_case', True) else 0,
            1 if responder.get('enabled', True) else 0, responder.get('created_by'), self._now()
        ))
        conn.commit()
        self._return_connection(conn)
        return responder_id

    def get_auto_responders(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM auto_responders WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        result = []
        for row in rows:
            data = dict(row)
            data['ignore_case'] = bool(data['ignore_case'])
            data['enabled'] = bool(data['enabled'])
            result.append(data)
        return result

    def get_auto_responder(self, guild_id: str, responder_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM auto_responders WHERE id = %s AND guild_id = %s', (responder_id, str(guild_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        data['ignore_case'] = bool(data['ignore_case'])
        data['enabled'] = bool(data['enabled'])
        return data

    def update_auto_responder(self, guild_id: str, responder_id: str, updates: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        for key, value in updates.items():
            if key in ['trigger_word', 'response', 'match_type', 'ignore_case', 'enabled']:
                if key in ['ignore_case', 'enabled']:
                    value = 1 if value else 0
                cursor.execute(f'UPDATE auto_responders SET {key} = %s WHERE id = %s AND guild_id = %s',
                              (value, responder_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    def delete_auto_responder(self, guild_id: str, responder_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM auto_responders WHERE id = %s AND guild_id = %s', (responder_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    # Global stats methods
    def get_total_warnings_count(self) -> int:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT COUNT(*) as count FROM warnings')
        count = cursor.fetchone()['count']
        self._return_connection(conn)
        return count

    def get_total_tickets_count(self) -> int:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT COUNT(*) as count FROM tickets')
        count = cursor.fetchone()['count']
        self._return_connection(conn)
        return count

    def get_total_transcripts_count(self) -> int:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT COUNT(*) as count FROM transcripts')
        count = cursor.fetchone()['count']
        self._return_connection(conn)
        return count

    def get_total_users_count(self) -> int:
        """Count unique users across all warnings and tickets."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) as count FROM (
                SELECT user_id FROM warnings
                UNION
                SELECT user_id FROM tickets
            ) AS combined_users
        ''')
        count = cursor.fetchone()['count']
        self._return_connection(conn)
        return count


    def create_giveaway(self, guild_id: str, giveaway: dict) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        giveaway_id = giveaway.get('id') or self._generate_id()
        cursor.execute('''
            INSERT INTO giveaways (id, guild_id, channel_id, message_id, prize, winners_count, host_id, required_role, ends_at, ended, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s)
        ''', (
            giveaway_id, str(guild_id), giveaway.get('channel_id'), giveaway.get('message_id'),
            giveaway.get('prize'), giveaway.get('winners_count', 1), giveaway.get('host_id'),
            giveaway.get('required_role'), giveaway.get('ends_at'), self._now()
        ))
        conn.commit()
        self._return_connection(conn)
        return giveaway_id

    def get_giveaway(self, giveaway_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM giveaways WHERE id = %s', (giveaway_id,))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        data['ended'] = bool(data['ended'])
        return data

    def get_giveaway_by_message(self, message_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM giveaways WHERE message_id = %s', (str(message_id),))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        data['ended'] = bool(data['ended'])
        return data

    def get_active_giveaways(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM giveaways WHERE ended = 0')
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def get_guild_giveaways(self, guild_id: str, include_ended: bool = False) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if include_ended:
            cursor.execute('SELECT * FROM giveaways WHERE guild_id = %s ORDER BY created_at DESC', (str(guild_id),))
        else:
            cursor.execute('SELECT * FROM giveaways WHERE guild_id = %s AND ended = 0 ORDER BY created_at DESC', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def update_giveaway(self, giveaway_id: str, updates: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        for key, value in updates.items():
            if key in ['message_id', 'ended', 'ends_at']:
                if key == 'ended':
                    value = 1 if value else 0
                cursor.execute(f'UPDATE giveaways SET {key} = %s WHERE id = %s', (value, giveaway_id))
        conn.commit()
        self._return_connection(conn)

    def end_giveaway(self, giveaway_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE giveaways SET ended = 1 WHERE id = %s', (giveaway_id,))
        conn.commit()
        self._return_connection(conn)

    def delete_giveaway(self, giveaway_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM giveaway_entries WHERE giveaway_id = %s', (giveaway_id,))
        cursor.execute('DELETE FROM giveaways WHERE id = %s', (giveaway_id,))
        conn.commit()
        self._return_connection(conn)

    def add_giveaway_entry(self, giveaway_id: str, user_id: str) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO giveaway_entries (giveaway_id, user_id, entered_at) VALUES (%s, %s, %s)',
                          (giveaway_id, str(user_id), self._now()))
            conn.commit()
            self._return_connection(conn)
            return True
        except psycopg2.IntegrityError:
            conn.rollback()
            self._return_connection(conn)
            return False

    def remove_giveaway_entry(self, giveaway_id: str, user_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM giveaway_entries WHERE giveaway_id = %s AND user_id = %s',
                      (giveaway_id, str(user_id)))
        conn.commit()
        self._return_connection(conn)

    def get_giveaway_entries(self, giveaway_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT user_id FROM giveaway_entries WHERE giveaway_id = %s', (giveaway_id,))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [row['user_id'] for row in rows]

    def get_giveaway_entry_count(self, giveaway_id: str) -> int:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT COUNT(*) as count FROM giveaway_entries WHERE giveaway_id = %s', (giveaway_id,))
        count = cursor.fetchone()['count']
        self._return_connection(conn)
        return count


    def set_afk(self, guild_id: str, user_id: str, reason: str = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO afk (guild_id, user_id, reason, set_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET reason = %s, set_at = %s
        ''', (str(guild_id), str(user_id), reason, self._now(), reason, self._now()))
        conn.commit()
        self._return_connection(conn)

    def get_afk(self, guild_id: str, user_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM afk WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def remove_afk(self, guild_id: str, user_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM afk WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)

    def get_afk_users(self, guild_id: str, user_ids: list) -> list:
        """Get AFK status for multiple users at once."""
        if not user_ids:
            return []
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        placeholders = ','.join(['%s'] * len(user_ids))
        cursor.execute(f'SELECT * FROM afk WHERE guild_id = %s AND user_id IN ({placeholders})',
                      [str(guild_id)] + [str(uid) for uid in user_ids])
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]


    def get_economy(self, guild_id: str, user_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM economy WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        if row:
            return dict(row)
        return {'guild_id': str(guild_id), 'user_id': str(user_id), 'balance': 0, 'bank': 0, 'last_daily': None, 'last_work': None, 'total_earned': 0}

    def update_economy(self, guild_id: str, user_id: str, updates: dict):
        conn = self._get_connection()
        cursor = conn.cursor()
        # First ensure the user exists
        cursor.execute('''
            INSERT INTO economy (guild_id, user_id, balance, bank, total_earned)
            VALUES (%s, %s, 0, 0, 0)
            ON CONFLICT (guild_id, user_id) DO NOTHING
        ''', (str(guild_id), str(user_id)))
        # Then update
        for key, value in updates.items():
            if key in ['balance', 'bank', 'last_daily', 'last_work', 'total_earned']:
                cursor.execute(f'UPDATE economy SET {key} = %s WHERE guild_id = %s AND user_id = %s',
                              (value, str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)

    def add_balance(self, guild_id: str, user_id: str, amount: int):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO economy (guild_id, user_id, balance, bank, total_earned)
            VALUES (%s, %s, %s, 0, %s)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                balance = economy.balance + %s,
                total_earned = economy.total_earned + %s
        ''', (str(guild_id), str(user_id), amount, max(0, amount), amount, max(0, amount)))
        conn.commit()
        self._return_connection(conn)

    def remove_balance(self, guild_id: str, user_id: str, amount: int) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT balance FROM economy WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        if not row or row['balance'] < amount:
            self._return_connection(conn)
            return False
        cursor.execute('UPDATE economy SET balance = balance - %s WHERE guild_id = %s AND user_id = %s',
                      (amount, str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)
        return True

    def transfer_balance(self, guild_id: str, from_user: str, to_user: str, amount: int) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT balance FROM economy WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(from_user)))
        row = cursor.fetchone()
        if not row or row['balance'] < amount:
            self._return_connection(conn)
            return False
        # Remove from sender
        cursor.execute('UPDATE economy SET balance = balance - %s WHERE guild_id = %s AND user_id = %s',
                      (amount, str(guild_id), str(from_user)))
        # Add to recipient
        cursor.execute('''
            INSERT INTO economy (guild_id, user_id, balance, bank, total_earned)
            VALUES (%s, %s, %s, 0, 0)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = economy.balance + %s
        ''', (str(guild_id), str(to_user), amount, amount))
        conn.commit()
        self._return_connection(conn)
        return True

    def deposit_bank(self, guild_id: str, user_id: str, amount: int) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT balance FROM economy WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        if not row or row['balance'] < amount:
            self._return_connection(conn)
            return False
        cursor.execute('UPDATE economy SET balance = balance - %s, bank = bank + %s WHERE guild_id = %s AND user_id = %s',
                      (amount, amount, str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)
        return True

    def withdraw_bank(self, guild_id: str, user_id: str, amount: int) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT bank FROM economy WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        if not row or row['bank'] < amount:
            self._return_connection(conn)
            return False
        cursor.execute('UPDATE economy SET bank = bank - %s, balance = balance + %s WHERE guild_id = %s AND user_id = %s',
                      (amount, amount, str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)
        return True

    def get_economy_leaderboard(self, guild_id: str, limit: int = 10) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('''
            SELECT user_id, balance, bank, (balance + bank) as total
            FROM economy WHERE guild_id = %s
            ORDER BY total DESC LIMIT %s
        ''', (str(guild_id), limit))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]


    def cache_invites(self, guild_id: str, invites: list):
        """Cache all invites for a guild."""
        conn = self._get_connection()
        cursor = conn.cursor()
        # Clear existing cache
        cursor.execute('DELETE FROM invite_cache WHERE guild_id = %s', (str(guild_id),))
        # Insert new cache
        for inv in invites:
            cursor.execute('''
                INSERT INTO invite_cache (guild_id, code, uses, inviter_id)
                VALUES (%s, %s, %s, %s)
            ''', (str(guild_id), inv['code'], inv['uses'], inv.get('inviter_id')))
        conn.commit()
        self._return_connection(conn)

    def get_cached_invites(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM invite_cache WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def update_invite_cache(self, guild_id: str, code: str, uses: int, inviter_id: str = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO invite_cache (guild_id, code, uses, inviter_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(guild_id, code) DO UPDATE SET uses = %s
        ''', (str(guild_id), code, uses, inviter_id, uses))
        conn.commit()
        self._return_connection(conn)

    def remove_invite_cache(self, guild_id: str, code: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM invite_cache WHERE guild_id = %s AND code = %s', (str(guild_id), code))
        conn.commit()
        self._return_connection(conn)

    def track_invite(self, guild_id: str, user_id: str, inviter_id: str, invite_code: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO invite_tracking (guild_id, user_id, inviter_id, invite_code, joined_at, "left")
            VALUES (%s, %s, %s, %s, %s, 0)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                inviter_id = %s, invite_code = %s, joined_at = %s, "left" = 0
        ''', (str(guild_id), str(user_id), str(inviter_id) if inviter_id else None, invite_code, self._now(),
              str(inviter_id) if inviter_id else None, invite_code, self._now()))
        conn.commit()
        self._return_connection(conn)

    def mark_user_left(self, guild_id: str, user_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE invite_tracking SET "left" = 1 WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)

    def get_user_inviter(self, guild_id: str, user_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM invite_tracking WHERE guild_id = %s AND user_id = %s',
                      (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def get_invite_stats(self, guild_id: str, inviter_id: str) -> dict:
        """Get invite statistics for a user."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Total invites
        cursor.execute('SELECT COUNT(*) as count FROM invite_tracking WHERE guild_id = %s AND inviter_id = %s',
                      (str(guild_id), str(inviter_id)))
        total = cursor.fetchone()['count']
        # Current (not left)
        cursor.execute('SELECT COUNT(*) as count FROM invite_tracking WHERE guild_id = %s AND inviter_id = %s AND "left" = 0',
                      (str(guild_id), str(inviter_id)))
        current = cursor.fetchone()['count']
        # Left
        cursor.execute('SELECT COUNT(*) as count FROM invite_tracking WHERE guild_id = %s AND inviter_id = %s AND "left" = 1',
                      (str(guild_id), str(inviter_id)))
        left = cursor.fetchone()['count']
        self._return_connection(conn)
        return {'total': total, 'current': current, 'left': left}

    def get_invite_leaderboard(self, guild_id: str, limit: int = 10) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('''
            SELECT inviter_id,
                   COUNT(*) as total,
                   SUM(CASE WHEN "left" = 0 THEN 1 ELSE 0 END) as current,
                   SUM(CASE WHEN "left" = 1 THEN 1 ELSE 0 END) as left_count
            FROM invite_tracking
            WHERE guild_id = %s AND inviter_id IS NOT NULL
            GROUP BY inviter_id
            ORDER BY current DESC
            LIMIT %s
        ''', (str(guild_id), limit))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    # Dev Auth Codes
    def create_dev_auth_code(self, code: str, guild_id: str, user_id: str, expires_at: str, max_uses: int = 1) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO dev_auth_codes (code, guild_id, user_id, created_at, expires_at, max_uses, use_count)
            VALUES (%s, %s, %s, %s, %s, %s, 0)
        ''', (code, str(guild_id), str(user_id), self._now(), expires_at, max_uses))
        conn.commit()
        self._return_connection(conn)
        return code

    def get_dev_auth_code(self, code: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM dev_auth_codes WHERE code = %s', (code,))
        row = cursor.fetchone()
        self._return_connection(conn)
        if not row:
            return None
        data = dict(row)
        # Handle legacy 'used' column - treat as max_uses=1, use_count=1 if used
        if 'max_uses' not in data:
            data['max_uses'] = 1
            data['use_count'] = 1 if data.get('used', 0) else 0
        return data

    def use_dev_auth_code(self, code: str, used_by: str) -> bool:
        """Increment use count for a dev auth code. Returns True if successful."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # First check if code has uses remaining
        cursor.execute('SELECT max_uses, use_count FROM dev_auth_codes WHERE code = %s', (code,))
        row = cursor.fetchone()
        if not row:
            self._return_connection(conn)
            return False
        max_uses = row['max_uses'] if row['max_uses'] is not None else 1
        use_count = row['use_count'] if row['use_count'] is not None else 0
        if use_count >= max_uses:
            self._return_connection(conn)
            return False
        # Increment use count
        cursor.execute('''
            UPDATE dev_auth_codes SET use_count = use_count + 1, used_by = %s, used_at = %s
            WHERE code = %s
        ''', (str(used_by), self._now(), code))
        conn.commit()
        updated = cursor.rowcount > 0
        self._return_connection(conn)
        return updated

    def cleanup_expired_dev_auth_codes(self):
        """Remove expired codes and fully used codes older than 24 hours."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM dev_auth_codes WHERE expires_at < %s OR (use_count >= max_uses AND used_at < %s)
        ''', (self._now(), self._now()))
        conn.commit()
        self._return_connection(conn)

    # Leveling System
    def get_user_level(self, guild_id: str, user_id: str) -> dict:
        """Get a user's leveling data."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM leveling WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        if row:
            return dict(row)
        return {'guild_id': str(guild_id), 'user_id': str(user_id), 'xp': 0, 'level': 0, 'total_messages': 0, 'last_xp_time': None}

    def add_xp(self, guild_id: str, user_id: str, xp_amount: int) -> dict:
        """Add XP to a user and return their updated data with level_up flag."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get current data
        cursor.execute('SELECT * FROM leveling WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        row = cursor.fetchone()

        if row:
            current_xp = row['xp'] + xp_amount
            current_level = row['level']
            total_messages = row['total_messages'] + 1
        else:
            current_xp = xp_amount
            current_level = 0
            total_messages = 1

        # Calculate new level (XP needed = 100 * (level + 1)^1.5)
        new_level = current_level
        while current_xp >= self._xp_for_level(new_level + 1):
            new_level += 1

        level_up = new_level > current_level

        # Upsert
        cursor.execute('''
            INSERT INTO leveling (guild_id, user_id, xp, level, total_messages, last_xp_time)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                xp = EXCLUDED.xp,
                level = EXCLUDED.level,
                total_messages = EXCLUDED.total_messages,
                last_xp_time = EXCLUDED.last_xp_time
        ''', (str(guild_id), str(user_id), current_xp, new_level, total_messages, self._now()))
        conn.commit()
        self._return_connection(conn)

        return {
            'xp': current_xp,
            'level': new_level,
            'total_messages': total_messages,
            'level_up': level_up,
            'old_level': current_level
        }

    def _xp_for_level(self, level: int) -> int:
        """Calculate total XP needed for a level."""
        if level <= 0:
            return 0
        # XP curve: 100 * level^1.5 (cumulative)
        total = 0
        for i in range(1, level + 1):
            total += int(100 * (i ** 1.5))
        return total

    def get_xp_for_next_level(self, current_level: int) -> int:
        """Get XP needed for the next level (not cumulative, just the increment)."""
        return int(100 * ((current_level + 1) ** 1.5))

    def get_level_leaderboard(self, guild_id: str, limit: int = 10) -> list:
        """Get the leveling leaderboard for a guild."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('''
            SELECT user_id, xp, level, total_messages
            FROM leveling
            WHERE guild_id = %s
            ORDER BY xp DESC
            LIMIT %s
        ''', (str(guild_id), limit))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def set_user_level(self, guild_id: str, user_id: str, level: int, xp: int = None):
        """Set a user's level (admin command)."""
        if xp is None:
            xp = self._xp_for_level(level)
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO leveling (guild_id, user_id, xp, level, total_messages, last_xp_time)
            VALUES (%s, %s, %s, %s, 0, %s)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                xp = EXCLUDED.xp,
                level = EXCLUDED.level,
                last_xp_time = EXCLUDED.last_xp_time
        ''', (str(guild_id), str(user_id), xp, level, self._now()))
        conn.commit()
        self._return_connection(conn)

    def reset_user_level(self, guild_id: str, user_id: str):
        """Reset a user's leveling data."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM leveling WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)

    def get_user_rank(self, guild_id: str, user_id: str) -> int:
        """Get a user's rank in the server."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('''
            SELECT COUNT(*) + 1 as rank FROM leveling
            WHERE guild_id = %s AND xp > (
                SELECT COALESCE(xp, 0) FROM leveling WHERE guild_id = %s AND user_id = %s
            )
        ''', (str(guild_id), str(guild_id), str(user_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        return row['rank'] if row else 1

    # Level Roles
    def get_level_roles(self, guild_id: str) -> list:
        """Get all level roles for a guild."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT level, role_id FROM level_roles WHERE guild_id = %s ORDER BY level', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def set_level_role(self, guild_id: str, level: int, role_id: str):
        """Set a role to be given at a certain level."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO level_roles (guild_id, level, role_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (guild_id, level) DO UPDATE SET role_id = %s
        ''', (str(guild_id), level, str(role_id), str(role_id)))
        conn.commit()
        self._return_connection(conn)

    def remove_level_role(self, guild_id: str, level: int):
        """Remove a level role."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM level_roles WHERE guild_id = %s AND level = %s', (str(guild_id), level))
        conn.commit()
        self._return_connection(conn)


    def create_voice_generator(self, guild_id: str, generator: dict) -> str:
        """Create a voice channel generator (Join to Create channel)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        generator_id = generator.get('id') or self._generate_id()
        cursor.execute('''
            INSERT INTO voice_generators (id, guild_id, channel_id, category_id, name_template, user_limit, bitrate, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                guild_id = %s, channel_id = %s, category_id = %s, name_template = %s,
                user_limit = %s, bitrate = %s, created_by = %s, created_at = %s
        ''', (
            generator_id, str(guild_id), generator.get('channel_id'), generator.get('category_id'),
            generator.get('name_template', "{user}'s Channel"), generator.get('user_limit', 0),
            generator.get('bitrate', 64000), generator.get('created_by'), self._now(),
            str(guild_id), generator.get('channel_id'), generator.get('category_id'),
            generator.get('name_template', "{user}'s Channel"), generator.get('user_limit', 0),
            generator.get('bitrate', 64000), generator.get('created_by'), self._now()
        ))
        conn.commit()
        self._return_connection(conn)
        return generator_id

    def get_voice_generator(self, guild_id: str, generator_id: str) -> dict:
        """Get a voice generator by ID."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM voice_generators WHERE id = %s AND guild_id = %s', (generator_id, str(guild_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def get_voice_generator_by_channel(self, channel_id: str) -> dict:
        """Get a voice generator by its trigger channel ID."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM voice_generators WHERE channel_id = %s', (str(channel_id),))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def get_all_voice_generators(self, guild_id: str) -> list:
        """Get all voice generators for a guild."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM voice_generators WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def update_voice_generator(self, guild_id: str, generator_id: str, updates: dict):
        """Update a voice generator."""
        conn = self._get_connection()
        cursor = conn.cursor()
        for key, value in updates.items():
            if key in ['name_template', 'user_limit', 'bitrate', 'category_id']:
                cursor.execute(f'UPDATE voice_generators SET {key} = %s WHERE id = %s AND guild_id = %s',
                              (value, generator_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    def delete_voice_generator(self, guild_id: str, generator_id: str):
        """Delete a voice generator."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM voice_generators WHERE id = %s AND guild_id = %s', (generator_id, str(guild_id)))
        conn.commit()
        self._return_connection(conn)

    def create_temp_voice_channel(self, guild_id: str, channel_id: str, owner_id: str, generator_id: str = None):
        """Track a temporary voice channel."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO temp_voice_channels (channel_id, guild_id, owner_id, generator_id, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (channel_id) DO UPDATE SET
                guild_id = %s, owner_id = %s, generator_id = %s, created_at = %s
        ''', (str(channel_id), str(guild_id), str(owner_id), generator_id, self._now(),
              str(guild_id), str(owner_id), generator_id, self._now()))
        conn.commit()
        self._return_connection(conn)

    def get_temp_voice_channel(self, channel_id: str) -> dict:
        """Get a temporary voice channel by ID."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM temp_voice_channels WHERE channel_id = %s', (str(channel_id),))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def get_user_temp_voice_channel(self, guild_id: str, owner_id: str) -> dict:
        """Get a user's temporary voice channel in a guild."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM temp_voice_channels WHERE guild_id = %s AND owner_id = %s',
                      (str(guild_id), str(owner_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def delete_temp_voice_channel(self, channel_id: str):
        """Delete a temporary voice channel record."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM temp_voice_channels WHERE channel_id = %s', (str(channel_id),))
        conn.commit()
        self._return_connection(conn)

    def get_all_temp_voice_channels(self, guild_id: str = None) -> list:
        """Get all temporary voice channels, optionally filtered by guild."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if guild_id:
            cursor.execute('SELECT * FROM temp_voice_channels WHERE guild_id = %s', (str(guild_id),))
        else:
            cursor.execute('SELECT * FROM temp_voice_channels')
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    # Incidents
    def create_incident(self, title: str, description: str, severity: str, affected_services: list, created_by: str) -> dict:
        """Create a new incident."""
        conn = self._get_connection()
        cursor = conn.cursor()
        incident_id = self._generate_id()[:8]  # Short ID for URLs
        now = self._now()
        cursor.execute('''
            INSERT INTO incidents (id, title, description, status, severity, affected_services, created_by, created_at, updated_at, updates)
            VALUES (%s, %s, %s, 'investigating', %s, %s, %s, %s, %s, '[]')
        ''', (incident_id, title, description, severity, json.dumps(affected_services), created_by, now, now))
        conn.commit()
        self._return_connection(conn)
        return {'id': incident_id, 'title': title, 'status': 'investigating', 'severity': severity, 'created_at': now}

    def get_incident(self, incident_id: str) -> dict:
        """Get an incident by ID."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM incidents WHERE id = %s', (incident_id,))
        row = cursor.fetchone()
        self._return_connection(conn)
        if row:
            incident = dict(row)
            incident['affected_services'] = json.loads(incident.get('affected_services', '[]'))
            incident['updates'] = json.loads(incident.get('updates', '[]'))
            return incident
        return None

    def get_all_incidents(self, include_resolved: bool = True, limit: int = 50) -> list:
        """Get all incidents, optionally excluding resolved ones."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if include_resolved:
            cursor.execute('SELECT * FROM incidents ORDER BY created_at DESC LIMIT %s', (limit,))
        else:
            cursor.execute("SELECT * FROM incidents WHERE status != 'resolved' ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = cursor.fetchall()
        self._return_connection(conn)
        incidents = []
        for row in rows:
            incident = dict(row)
            incident['affected_services'] = json.loads(incident.get('affected_services', '[]'))
            incident['updates'] = json.loads(incident.get('updates', '[]'))
            incidents.append(incident)
        return incidents

    def update_incident(self, incident_id: str, updates: dict) -> bool:
        """Update an incident."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get current incident
        cursor.execute('SELECT * FROM incidents WHERE id = %s', (incident_id,))
        row = cursor.fetchone()
        if not row:
            self._return_connection(conn)
            return False

        current = dict(row)
        now = self._now()

        # Handle affected_services if it's a list
        if 'affected_services' in updates and isinstance(updates['affected_services'], list):
            updates['affected_services'] = json.dumps(updates['affected_services'])

        # Build update query
        set_clauses = ['updated_at = %s']
        values = [now]

        for key in ['title', 'description', 'status', 'severity', 'affected_services']:
            if key in updates:
                set_clauses.append(f'{key} = %s')
                values.append(updates[key])

        # Set resolved_at if status is resolved
        if updates.get('status') == 'resolved' and not current.get('resolved_at'):
            set_clauses.append('resolved_at = %s')
            values.append(now)

        values.append(incident_id)
        cursor.execute(f"UPDATE incidents SET {', '.join(set_clauses)} WHERE id = %s", values)
        conn.commit()
        self._return_connection(conn)
        return True

    def add_incident_update(self, incident_id: str, message: str, status: str = None, updated_by: str = None) -> bool:
        """Add an update to an incident."""
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute('SELECT updates, status FROM incidents WHERE id = %s', (incident_id,))
        row = cursor.fetchone()
        if not row:
            self._return_connection(conn)
            return False

        updates = json.loads(row['updates'] or '[]')
        now = self._now()

        update_entry = {
            'message': message,
            'timestamp': now,
            'updated_by': updated_by
        }
        if status:
            update_entry['status'] = status

        updates.append(update_entry)

        # Update the incident
        set_clauses = ['updates = %s', 'updated_at = %s']
        values = [json.dumps(updates), now]

        if status:
            set_clauses.append('status = %s')
            values.append(status)
            if status == 'resolved':
                set_clauses.append('resolved_at = %s')
                values.append(now)

        values.append(incident_id)
        cursor.execute(f"UPDATE incidents SET {', '.join(set_clauses)} WHERE id = %s", values)
        conn.commit()
        self._return_connection(conn)
        return True

    def delete_incident(self, incident_id: str) -> bool:
        """Delete an incident."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM incidents WHERE id = %s', (incident_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        self._return_connection(conn)
        return deleted


    # Tempbans
    def add_tempban(self, guild_id: str, user_id: str, moderator_id: str, reason: str, expires_at: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        tempban_id = self._generate_id()
        cursor.execute('''
            INSERT INTO tempbans (id, guild_id, user_id, moderator_id, reason, banned_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (tempban_id, str(guild_id), str(user_id), str(moderator_id), reason, self._now(), expires_at))
        conn.commit()
        self._return_connection(conn)
        return tempban_id

    def get_expired_tempbans(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM tempbans WHERE expires_at <= %s', (self._now(),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def remove_tempban(self, tempban_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM tempbans WHERE id = %s', (tempban_id,))
        conn.commit()
        self._return_connection(conn)

    def remove_tempban_by_user(self, guild_id: str, user_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM tempbans WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        conn.commit()
        self._return_connection(conn)

    def get_tempbans(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM tempbans WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    # Starboard
    def get_starboard_post(self, guild_id: str, original_message_id: str) -> dict | None:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM starboard_posts WHERE guild_id = %s AND original_message_id = %s',
                      (str(guild_id), str(original_message_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def save_starboard_post(self, guild_id: str, original_message_id: str, starboard_message_id: str, channel_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO starboard_posts (guild_id, original_message_id, starboard_message_id, channel_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (guild_id, original_message_id) DO UPDATE SET
                starboard_message_id = %s, channel_id = %s
        ''', (str(guild_id), str(original_message_id), str(starboard_message_id), str(channel_id),
              str(starboard_message_id), str(channel_id)))
        conn.commit()
        self._return_connection(conn)

    def delete_starboard_post(self, guild_id: str, original_message_id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM starboard_posts WHERE guild_id = %s AND original_message_id = %s',
                      (str(guild_id), str(original_message_id)))
        conn.commit()
        self._return_connection(conn)


    def save_ticket_feedback(self, guild_id: str, ticket_number: int, user_id: str, rating: int):
        conn = self._get_connection()
        cursor = conn.cursor()
        feedback_id = self._generate_id()
        cursor.execute('''
            INSERT INTO ticket_feedback (id, guild_id, ticket_number, user_id, rating, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        ''', (feedback_id, str(guild_id), ticket_number, str(user_id), rating, self._now()))
        conn.commit()
        self._return_connection(conn)
        return feedback_id

    def get_avg_ticket_rating(self, guild_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT AVG(rating) as avg_rating, COUNT(*) as count FROM ticket_feedback WHERE guild_id = %s',
                      (str(guild_id),))
        row = cursor.fetchone()
        self._return_connection(conn)
        avg = row['avg_rating']
        return {'avg_rating': round(float(avg), 2) if avg else 0, 'count': row['count']}


    def create_snippet(self, guild_id: str, name: str, content: str, created_by: str) -> str:
        conn = self._get_connection()
        cursor = conn.cursor()
        snippet_id = self._generate_id()
        cursor.execute('''
            INSERT INTO snippets (id, guild_id, name, content, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (guild_id, name) DO UPDATE SET content = %s, created_by = %s, created_at = %s
        ''', (snippet_id, str(guild_id), name.lower(), content, str(created_by), self._now(),
              content, str(created_by), self._now()))
        conn.commit()
        self._return_connection(conn)
        return snippet_id

    def get_snippet(self, guild_id: str, name: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM snippets WHERE guild_id = %s AND name = %s', (str(guild_id), name.lower()))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def get_all_snippets(self, guild_id: str) -> list:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM snippets WHERE guild_id = %s ORDER BY name', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def delete_snippet(self, guild_id: str, name: str) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM snippets WHERE guild_id = %s AND name = %s', (str(guild_id), name.lower()))
        deleted = cursor.rowcount > 0
        conn.commit()
        self._return_connection(conn)
        return deleted


    def add_ticket_ban(self, guild_id: str, user_id: str, banned_by: str, reason: str = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO ticket_bans (guild_id, user_id, banned_by, reason, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (guild_id, user_id) DO NOTHING
        ''', (str(guild_id), str(user_id), str(banned_by), reason, self._now()))
        conn.commit()
        self._return_connection(conn)

    def remove_ticket_ban(self, guild_id: str, user_id: str) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM ticket_bans WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        deleted = cursor.rowcount > 0
        conn.commit()
        self._return_connection(conn)
        return deleted

    def is_ticket_banned(self, guild_id: str, user_id: str) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM ticket_bans WHERE guild_id = %s AND user_id = %s', (str(guild_id), str(user_id)))
        row = cursor.fetchone()
        self._return_connection(conn)
        return row is not None


    def get_ticket_stats(self, guild_id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT COUNT(*) as count FROM tickets WHERE guild_id = %s AND status = %s',
                      (str(guild_id), 'open'))
        open_count = cursor.fetchone()['count']
        cursor.execute('SELECT COUNT(*) as count FROM transcripts WHERE guild_id = %s', (str(guild_id),))
        closed_count = cursor.fetchone()['count']
        cursor.execute('SELECT extra FROM transcripts WHERE guild_id = %s', (str(guild_id),))
        rows = cursor.fetchall()
        staff_claims = {}
        for row in rows:
            import json
            extra = json.loads(row['extra']) if row.get('extra') else {}
            claimed_by = extra.get('claimed_by')
            if claimed_by:
                staff_claims[claimed_by] = staff_claims.get(claimed_by, 0) + 1
        self._return_connection(conn)
        busiest_staff = max(staff_claims, key=staff_claims.get) if staff_claims else None
        busiest_count = staff_claims.get(busiest_staff, 0) if busiest_staff else 0
        return {
            'open': open_count,
            'closed': closed_count,
            'total': open_count + closed_count,
            'busiest_staff': busiest_staff,
            'busiest_count': busiest_count,
        }

    # LOA
    def create_loa(self, guild_id, user_id, reason, start_date, end_date):
        conn = self._get_connection()
        cursor = conn.cursor()
        loa_id = self._generate_id()
        cursor.execute('''
            INSERT INTO loa_requests (id, guild_id, user_id, reason, start_date, end_date, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
        ''', (loa_id, str(guild_id), str(user_id), reason, start_date, end_date, self._now()))
        conn.commit()
        self._return_connection(conn)
        return loa_id

    def get_loa(self, guild_id, loa_id):
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM loa_requests WHERE guild_id = %s AND id = %s', (str(guild_id), loa_id))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def get_user_active_loa(self, guild_id, user_id):
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM loa_requests WHERE guild_id = %s AND user_id = %s AND status IN (%s, %s)',
                      (str(guild_id), str(user_id), 'pending', 'approved'))
        row = cursor.fetchone()
        self._return_connection(conn)
        return dict(row) if row else None

    def get_all_loa(self, guild_id, status=None):
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if status:
            cursor.execute('SELECT * FROM loa_requests WHERE guild_id = %s AND status = %s ORDER BY created_at DESC',
                          (str(guild_id), status))
        else:
            cursor.execute('SELECT * FROM loa_requests WHERE guild_id = %s ORDER BY created_at DESC', (str(guild_id),))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def update_loa_status(self, guild_id, loa_id, status, by_user_id=None):
        conn = self._get_connection()
        cursor = conn.cursor()
        if status == 'approved':
            cursor.execute('UPDATE loa_requests SET status = %s, approved_by = %s WHERE guild_id = %s AND id = %s',
                          (status, str(by_user_id) if by_user_id else None, str(guild_id), loa_id))
        elif status == 'denied':
            cursor.execute('UPDATE loa_requests SET status = %s, denied_by = %s WHERE guild_id = %s AND id = %s',
                          (status, str(by_user_id) if by_user_id else None, str(guild_id), loa_id))
        else:
            cursor.execute('UPDATE loa_requests SET status = %s WHERE guild_id = %s AND id = %s',
                          (status, str(guild_id), loa_id))
        updated = cursor.rowcount > 0
        conn.commit()
        self._return_connection(conn)
        return updated

    def get_expired_loas(self):
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute('SELECT * FROM loa_requests WHERE status = %s AND end_date <= %s',
                      ('approved', self._now()[:10]))
        rows = cursor.fetchall()
        self._return_connection(conn)
        return [dict(row) for row in rows]

    def delete_loa(self, guild_id, loa_id):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM loa_requests WHERE guild_id = %s AND id = %s', (str(guild_id), loa_id))
        deleted = cursor.rowcount > 0
        conn.commit()
        self._return_connection(conn)
        return deleted


# Create singleton instance
db = Database()
