import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, send_from_directory, g, url_for

# --- Configuration ---
DATABASE = 'database.db'
UPLOAD_FOLDER = 'audio_uploads'
ALLOWED_EXTENSIONS = {'webm', 'mp3', 'ogg', 'wav', 'm4a'} # Added m4a often used by browsers
# Make sure to set a strong secret key in a real application!
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-replace-in-prod!') # Use environment variable ideally

# --- App Setup ---
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # Good practice for session cookies
app.config['SESSION_COOKIE_SECURE'] = False # Set to True if using HTTPS
app.config['SESSION_PERMANENT'] = True
app.permanent_session_lifetime = timedelta(days=31) # Session lifetime

# --- Database Helper Functions ---
def get_db():
    """Opens a new database connection if there is none yet for the current application context."""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row # Return rows as dictionary-like objects
    return g.db

@app.teardown_appcontext
def close_db(error):
    """Closes the database again at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db(force=False):
    """Initializes the database schema."""
    db_needs_init = not os.path.exists(DATABASE) or os.path.getsize(DATABASE) == 0 or force
    if not db_needs_init:
        # Quick check if tables exist
        try:
            conn = sqlite3.connect(DATABASE)
            conn.execute("SELECT COUNT(*) FROM CoupleState").fetchone()
            conn.close()
            print("Database appears initialized.")
            return # Don't re-initialize if tables exist and not forced
        except sqlite3.OperationalError:
            print("Database tables missing or corrupted. Re-initializing.")
            db_needs_init = True # Force init if check fails

    if db_needs_init:
        print(f"Initializing database schema (Force={force})...")
        try:
            # Ensure connection is established before reading schema
            conn = sqlite3.connect(DATABASE)
            conn.close()

            db = get_db() # Get connection within context
            schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
            if not os.path.exists(schema_path):
                 print(f"ERROR: schema.sql not found at {schema_path}")
                 return
            with app.open_resource('schema.sql', mode='r') as f:
                db.cursor().executescript(f.read())
            db.commit()
            print("Database schema executed.")

            # Initialize couple state if not present (should always happen after executescript)
            cur = db.execute("SELECT COUNT(*) FROM CoupleState WHERE id = 1")
            if cur.fetchone()[0] == 0:
                print("Initializing default couple state...")
                # Use yesterday to allow immediate streak increment on first point today
                yesterday_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                db.execute(
                    "INSERT INTO CoupleState (id, love_points, streak_count, last_streak_update_date) VALUES (?, ?, ?, ?)",
                    (1, 0, 0, yesterday_str) # Start with 0 points, 0 streak, last update "yesterday"
                )
                db.commit()
                print("Default couple state added.")
            else:
                 print("CoupleState row already exists.")
        except Exception as e:
             print(f"ERROR during DB initialization: {e}")


@app.cli.command('initdb')
def initdb_command():
    """Flask command to initialize the database."""
    with app.app_context():
        init_db(force=True) # Force init when command is run
        print("Database initialization complete via command.")


# --- Utility Functions ---
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_today_str():
    return datetime.now().strftime('%Y-%m-%d')

def get_yesterday_str():
    return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

# Ensure upload folder exists on startup
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    print(f"Created upload folder: {UPLOAD_FOLDER}")

# --- Routes ---
def initialize_database_on_startup():
    # Initialize DB when the app first starts if needed
    init_db()

with app.app_context():
    initialize_database_on_startup()

@app.route('/')
def index():
    """Serves the main HTML page."""
    # No longer need to initialize session flags
    return render_template('index.html')

@app.route('/api/state', methods=['GET'])
def get_state():
    """Gets the current couple state (points, streak) and today's progress."""
    try:
        db = get_db()
        cursor = db.execute("""
            SELECT love_points, streak_count, last_streak_update_date,
                   daily_progress_date, daily_random_answered, daily_manual_answered
            FROM CoupleState WHERE id = 1
        """)
        state_row = cursor.fetchone()
        if state_row:
            today_str = get_today_str()
            # Check if the progress flags are for today
            random_done = state_row["daily_random_answered"] if state_row["daily_progress_date"] == today_str else 0
            manual_done = state_row["daily_manual_answered"] if state_row["daily_progress_date"] == today_str else 0

            return jsonify({
                "lovePoints": state_row["love_points"],
                "streak": state_row["streak_count"],
                "lastStreakUpdateDate": state_row["last_streak_update_date"],
                "dailyRandomAnswered": random_done, # Boolean (0 or 1)
                "dailyManualAnswered": manual_done   # Boolean (0 or 1)
            })
        else:
            # Should not happen if initdb ran correctly, but handle defensively
            print("ERROR: CoupleState row not found!")
            return jsonify({"error": "Could not retrieve couple state"}), 500
    except Exception as e:
         print(f"ERROR in /api/state: {e}")
         return jsonify({"error": "Database error fetching state"}), 500


@app.route('/api/history', methods=['GET'])
def get_history():
    """Gets the question and answer history."""
    try:
        db = get_db()
        # Fetch all questions first
        questions_cur = db.execute("SELECT id, text FROM Questions ORDER BY id DESC") # Or order as needed
        questions = {row["id"]: {"id": row["id"], "text": row["text"], "answers": {}} for row in questions_cur.fetchall()}

        # Fetch all answers and populate the questions dict
        answers_cur = db.execute(
            "SELECT question_id, user_id, audio_filename, timestamp FROM Answers ORDER BY timestamp DESC"
        )
        for answer_row in answers_cur.fetchall():
            q_id = answer_row["question_id"]
            if q_id in questions:
                user = answer_row["user_id"]
                # Construct audio URL using Flask's url_for for robustness
                audio_url = url_for('serve_audio', filename=answer_row['audio_filename'], _external=False) # Relative URL
                # Only keep the latest answer per user per question for simplicity in display
                # If you need full history, adjust the logic
                if user not in questions[q_id]["answers"] or answer_row["timestamp"] > questions[q_id]["answers"][user]["timestamp"]:
                     questions[q_id]["answers"][user] = {
                        "audioUrl": audio_url,
                        "timestamp": answer_row["timestamp"]
                    }

        # Convert dict back to list for JSON response, ordered perhaps by latest answer overall
        history_list = sorted(list(questions.values()), key=lambda q: max(
            (a.get('timestamp', 0) for a in q.get('answers', {}).values()), default=0 # Handle questions with no answers
        ), reverse=True)

        return jsonify(history_list)
    except Exception as e:
         print(f"ERROR in /api/history: {e}")
         return jsonify({"error": "Database error fetching history"}), 500

@app.route('/api/answer', methods=['POST'])
def post_answer():
    """Handles saving a new answer, audio upload, and updates points/streak based on daily completion."""
    # --- Add this logging ---
    print("--- /api/answer Request ---")
    print("Request Headers:", request.headers)
    print("Request Form Data:", request.form)
    print("Request Files:", request.files)
    print("---------------------------")
    # --- End logging ---

    db = get_db()
    cursor = db.cursor() # Use cursor for transaction control

    # --- Input Validation ---
    if 'userId' not in request.form or request.form['userId'] not in ['nidhi', 'arpan']:
        print("Validation failed: Missing or invalid userId. Form:", request.form) # Add detail
        return jsonify({"error": "Missing or invalid userId"}), 400
    if 'questionText' not in request.form or not request.form['questionText']:
        print("Validation failed: Missing questionText. Form:", request.form) # Added similar detail here too
        return jsonify({"error": "Missing questionText"}), 400
    if 'source' not in request.form or request.form['source'] not in ['manual', 'random']:
         print("Validation failed: Missing or invalid source. Form:", request.form) # Added similar detail here too
         return jsonify({"error": "Missing or invalid source"}), 400
    if 'audioFile' not in request.files:
        print("Validation failed: Missing audioFile. Files:", request.files) # Added similar detail here too
        return jsonify({"error": "Missing audioFile"}), 400

    user_id = request.form['userId']
    question_text = request.form['questionText'].strip()
    source = request.form['source'] # 'manual' or 'random'
    audio_file = request.files['audioFile']

    if not audio_file or not allowed_file(audio_file.filename):
        return jsonify({"error": "Invalid audio file type"}), 400

    # --- Transaction Start ---
    try:
        # --- Process Question (Keep as is) ---
        cursor.execute("SELECT id FROM Questions WHERE text = ?", (question_text,))
        question_row = cursor.fetchone()
        if question_row:
            question_id = question_row["id"]
        else:
            cursor.execute("INSERT INTO Questions (text) VALUES (?)", (question_text,))
            question_id = cursor.lastrowid

        # --- Save Audio File (Keep as is) ---
        ext = audio_file.filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4()}.{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        audio_file.save(filepath)
        print(f"Audio saved to: {filepath}")

        # --- Save Answer Record (Keep as is) ---
        timestamp = int(datetime.now().timestamp() * 1000) # Milliseconds
        cursor.execute(
            "INSERT INTO Answers (question_id, user_id, audio_filename, timestamp) VALUES (?, ?, ?, ?)",
            (question_id, user_id, unique_filename, timestamp)
        )

        # --- REVISED Point and Streak Logic ---
        points_to_add = 0
        other_user = 'arpan' if user_id == 'nidhi' else 'nidhi'

        # Check if the other user already answered THIS question (before this current answer)
        cursor.execute(
            "SELECT COUNT(*) FROM Answers WHERE question_id = ? AND user_id = ?",
            (question_id, other_user)
        )
        other_answered_count = cursor.fetchone()[0]

        if other_answered_count == 0:
            # This is the first answer for this question
            points_to_add = 1
            print(f"Point logic: First answer for question {question_id}. Awarding +1 point.")
        else:
            # This is the second answer for this question
            points_to_add = 5
            print(f"Point logic: Second answer for question {question_id}. Awarding +5 points.")

        point_awarded_this_time = points_to_add # Store the amount awarded (1, 5, or 0 if error)

        # Fetch current state
        cursor.execute("""
            SELECT love_points, streak_count, last_streak_update_date,
                   daily_progress_date, daily_random_answered, daily_manual_answered
            FROM CoupleState WHERE id = 1
        """)
        state = cursor.fetchone()
        if not state: raise Exception("CoupleState not found during update")

        current_points = state["love_points"]
        current_streak = state["streak_count"]
        last_update_date_str = state["last_streak_update_date"]

        new_points = current_points + points_to_add
        new_streak = current_streak
        new_last_update_date = last_update_date_str
        today_str = get_today_str()
        yesterday_str = get_yesterday_str()

        # Update streak only if points were awarded AND it's the first time today
        if points_to_add > 0 and last_update_date_str != today_str:
            print(f"Streak logic: Points awarded ({points_to_add}) and first time today.")
            if last_update_date_str == yesterday_str:
                new_streak += 1
                print(f"Streak extended to {new_streak}")
            else: # Missed a day or first time
                new_streak = 1
                print(f"Streak reset/started to {new_streak}")
            new_last_update_date = today_str
        elif points_to_add > 0:
            print("Streak logic: Points awarded, but streak already updated today.")
        else:
            print("Streak logic: No points awarded this time.")


        # --- Daily Progress Flags (for UI indicator - separate from point logic) ---
        daily_progress_date = state["daily_progress_date"]
        daily_random = state["daily_random_answered"]
        daily_manual = state["daily_manual_answered"]

        # Reset daily flags if it's a new day
        if daily_progress_date != today_str:
            print(f"Daily flags: New day detected ({today_str}). Resetting flags.")
            daily_random = 0
            daily_manual = 0
            daily_progress_date = today_str # Update the date marker

        # Mark the current type as done for today (if not already)
        if source == 'random' and not daily_random:
            daily_random = 1
            print("Daily flags: Random marked as done for today.")
        elif source == 'manual' and not daily_manual:
            daily_manual = 1
            print("Daily flags: Manual marked as done for today.")
        # No 'else' needed, flags just track completion for the UI

        # --- Update Database State ---
        cursor.execute(
            """UPDATE CoupleState
               SET love_points = ?, streak_count = ?, last_streak_update_date = ?,
                   daily_progress_date = ?, daily_random_answered = ?, daily_manual_answered = ?
               WHERE id = 1""",
            (new_points, new_streak, new_last_update_date,
             daily_progress_date, daily_random, daily_manual)
        )
        print(f"DB State Updated: Points={new_points}, Streak={new_streak}, LastUpdate={new_last_update_date}, DailyProgressDate={daily_progress_date}, RndDone={daily_random}, MnlDone={daily_manual}")

        # --- Commit Transaction ---
        db.commit()
        print("Transaction committed.")

    except Exception as e:
        db.rollback() # Rollback DB changes on any error
        print(f"ERROR during answer processing, transaction rolled back: {e}")
        # File cleanup (Keep as is)
        if 'filepath' in locals() and os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"Cleaned up audio file: {filepath}")
            except OSError as remove_err:
                print(f"Error cleaning up audio file {filepath}: {remove_err}")
        return jsonify({"error": f"Failed to process answer: {e}"}), 500

    # --- Fetch final state after successful commit ---
    cursor.execute("""
        SELECT love_points, streak_count, last_streak_update_date,
               daily_random_answered, daily_manual_answered, daily_progress_date
        FROM CoupleState WHERE id = 1
    """)
    final_state = cursor.fetchone()

    # Ensure today's date is reflected if needed
    today_str_check = get_today_str()
    current_daily_random = final_state["daily_random_answered"] if final_state and final_state["daily_progress_date"] == today_str_check else 0
    current_daily_manual = final_state["daily_manual_answered"] if final_state and final_state["daily_progress_date"] == today_str_check else 0

    return jsonify({
        "message": "Answer saved successfully",
        "pointAwarded": point_awarded_this_time, # Send actual points awarded (0, 1, or 5)
        "lovePoints": final_state["love_points"] if final_state else 0,
        "streak": final_state["streak_count"] if final_state else 0,
        "lastStreakUpdateDate": final_state["last_streak_update_date"] if final_state else get_yesterday_str(),
        "dailyRandomAnswered": current_daily_random, # Send today's status
        "dailyManualAnswered": current_daily_manual  # Send today's status
    }), 201 # 201 Created


@app.route('/api/audio/<path:filename>')
def serve_audio(filename):
    """Serves an uploaded audio file."""
    print(f"Attempting to serve audio: {filename}")
    # Security: Basic check to prevent directory traversal,
    # though send_from_directory handles much of this.
    if '..' in filename or filename.startswith('/'):
        return jsonify({"error": "Invalid filename"}), 400
    try:
        return send_from_directory(
            os.path.abspath(app.config['UPLOAD_FOLDER']), # Use absolute path for safety
            filename,
            as_attachment=False # Play inline
        )
    except FileNotFoundError:
        print(f"Audio file not found: {filename}")
        return jsonify({"error": "Audio file not found"}), 404
    except Exception as e:
        print(f"Error serving audio file {filename}: {e}")
        return jsonify({"error": "Error serving audio file"}), 500


@app.route('/api/pending/<user_id>', methods=['GET'])
def get_pending_questions(user_id):
    """Gets questions answered by the other user but not yet by this user."""
    if user_id not in ['nidhi', 'arpan']:
        return jsonify({"error": "Invalid user ID"}), 400

    other_user = 'arpan' if user_id == 'nidhi' else 'nidhi'
    db = get_db()

    try:
        # Find questions the other user answered
        cursor = db.execute("""
            SELECT DISTINCT q.id, q.text
            FROM Questions q
            JOIN Answers a ON q.id = a.question_id
            WHERE a.user_id = ?
        """, (other_user,))
        other_user_answered_questions = {row['id']: row['text'] for row in cursor.fetchall()}

        if not other_user_answered_questions:
            return jsonify([]) # No questions answered by the other user yet

        # Find questions the current user answered
        cursor = db.execute("""
            SELECT DISTINCT question_id
            FROM Answers
            WHERE user_id = ? AND question_id IN ({})
        """.format(','.join('?'*len(other_user_answered_questions))),
        (user_id, *other_user_answered_questions.keys())) # Pass keys as parameters

        current_user_answered_ids = {row['question_id'] for row in cursor.fetchall()}

        # Filter out questions the current user ALSO answered
        pending_questions = []
        for q_id, q_text in other_user_answered_questions.items():
            if q_id not in current_user_answered_ids:
                 # Get the timestamp of when the other user answered for sorting
                cur_ts = db.execute("SELECT MAX(timestamp) FROM Answers WHERE question_id = ? AND user_id = ?", (q_id, other_user))
                timestamp = cur_ts.fetchone()[0] or 0
                pending_questions.append({"id": q_id, "text": q_text, "asked_by": other_user, "timestamp": timestamp})

        # Sort by when the other user answered (most recent first)
        pending_questions.sort(key=lambda x: x['timestamp'], reverse=True)

        return jsonify(pending_questions)

    except Exception as e:
        print(f"Error fetching pending questions for {user_id}: {e}")
        return jsonify({"error": "Database error fetching pending questions"}), 500


# --- Main Execution ---
if __name__ == '__main__':
    print("Starting Flask app...")
    with app.app_context():
        init_db() # Ensure DB is initialized before first request
    # Recommended host='0.0.0.0' to be accessible on your network
    # Use debug=True only for development, REMOVE for production
    app.run(host='0.0.0.0', port=9005, debug=True)