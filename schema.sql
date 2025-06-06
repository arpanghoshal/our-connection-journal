-- schema.sql --
DROP TABLE IF EXISTS CoupleState;
DROP TABLE IF EXISTS Questions;
DROP TABLE IF EXISTS Answers;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY CHECK (id IN ('partner1', 'partner2')),
    points INTEGER DEFAULT 0,
    streak INTEGER DEFAULT 0,
    last_answer_date TEXT
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    category TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS answers (
    question_id TEXT NOT NULL,
    user_id TEXT NOT NULL CHECK (user_id IN ('partner1', 'partner2')),
    audio_url TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (question_id, user_id),
    FOREIGN KEY (question_id) REFERENCES questions(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Insert default users
INSERT OR IGNORE INTO users (id) VALUES ('partner1'), ('partner2');

CREATE TABLE CoupleState (
  id INTEGER PRIMARY KEY, -- Only one row with id=1
  love_points INTEGER NOT NULL DEFAULT 0,
  streak_count INTEGER NOT NULL DEFAULT 0,
  last_streak_update_date TEXT, -- Storing date as YYYY-MM-DD string
  daily_progress_date TEXT DEFAULT '1970-01-01', -- Track which day's progress we're on
  daily_random_answered INTEGER DEFAULT 0, -- Boolean (0 or 1)
  daily_manual_answered INTEGER DEFAULT 0  -- Boolean (0 or 1)
);

CREATE TABLE Questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT UNIQUE NOT NULL
);

CREATE TABLE Answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER NOT NULL,
  user_id TEXT NOT NULL, -- 'partner1' or 'partner2'
  audio_filename TEXT NOT NULL,
  timestamp INTEGER NOT NULL, -- Store as Unix timestamp (milliseconds)
  FOREIGN KEY (question_id) REFERENCES Questions (id)
);

-- Optional: Insert initial state directly if init_db function feels complex
-- INSERT OR IGNORE INTO CoupleState (id, love_points, streak_count, last_streak_update_date)
-- VALUES (1, 0, 0, date('now', '-1 day')); -- Example for SQLite date function