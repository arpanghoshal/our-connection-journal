from app import app

if __name__ == "__main__":
    # This part is typically not run by Gunicorn, 
    # but can be useful for direct execution testing if needed.
    # Gunicorn will directly use the 'app' object imported above.
    # For production, rely on Gunicorn or another WSGI server.
    # app.run(debug=True) # Avoid running in debug mode in production setup
    pass # Keep this minimal 