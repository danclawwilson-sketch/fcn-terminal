# Entry point for Render deployment
# Imports the Flask app from fcn-terminal-app.py

from fcn_terminal_app import app

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
