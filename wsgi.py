# WSGI entry point for Gunicorn
from fcn_terminal_app import app

if __name__ == "__main__":
    app.run()
