# Kanban Markdown Renderer

A lightweight Flask application that reads an Obsidian Kanban-formatted Markdown file and renders it as a read-only Kanban board.

## Features

- Reads Markdown files in Obsidian Kanban format
- Renders columns (## headers) and cards (- [ ] tasks) as a Kanban board
- Read-only display (no editing capabilities)
- Modern, responsive design
- Lightweight and easy to maintain

## Markdown Format

The application expects Markdown files in Obsidian Kanban format:

```markdown
## Backlog
- [ ] Task 1
- [ ] Task 2

## In Progress
- [ ] Task 3

## Done
- [x] Completed task
```

- **Columns**: Headers starting with `##` (e.g., `## Backlog`)
- **Cards**: Task items using `- [ ]` (incomplete) or `- [x]` (completed)

## Installation

1. Clone this repository or copy files to your server

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

Or use a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

The Markdown file path can be configured via environment variable:

```bash
export KANBAN_MARKDOWN_FILE="/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
```

Default path: `/srv/kardinal/kardinal_public/Grafana Labs Kanban.md`

## Running the Application

### Development Mode

```bash
python app.py
```

The application will run on `http://127.0.0.1:5000`

### Production with Gunicorn (Recommended)

Install Gunicorn:
```bash
pip install gunicorn
```

Run with Gunicorn:
```bash
gunicorn -w 2 -b 127.0.0.1:5000 app:app
```

### Systemd Service

Create a systemd service file at `/etc/systemd/system/kanban-board.service`:

```ini
[Unit]
Description=Kanban Board Flask Application
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/kardinal
Environment="PATH=/path/to/kardinal/venv/bin"
Environment="KANBAN_MARKDOWN_FILE=/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
ExecStart=/path/to/kardinal/venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Replace `/path/to/kardinal` with your actual deployment path.

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable kanban-board
sudo systemctl start kanban-board
```

Check status:
```bash
sudo systemctl status kanban-board
```

## Traefik Configuration

Since you have Traefik installed, configure it to route `kardinal.nvdh.dev` to the Flask application.

### Option 1: Docker Labels (if using Docker)

If you're running the Flask app in Docker, add these labels to your container:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.kanban.rule=Host(`kardinal.nvdh.dev`)"
  - "traefik.http.routers.kanban.entrypoints=websecure"
  - "traefik.http.routers.kanban.tls.certresolver=letsencrypt"
  - "traefik.http.services.kanban.loadbalancer.server.port=5000"
```

### Option 2: File-based Configuration

Add to your Traefik configuration file (typically `traefik.yml` or `/etc/traefik/traefik.yml`):

```yaml
http:
  routers:
    kanban:
      rule: "Host(`kardinal.nvdh.dev`)"
      entryPoints:
        - websecure
      service: kanban
      tls:
        certResolver: letsencrypt
  services:
    kanban:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:5000"
```

After updating Traefik configuration, reload Traefik:
```bash
sudo systemctl reload traefik
```

## File Permissions

Ensure the application user (e.g., `www-data`) has read access to the Markdown file:

```bash
sudo chmod 644 "/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
sudo chown www-data:www-data "/srv/kardinal/kardinal_public/Grafana Labs Kanban.md"
```

Or if the file is in a Syncthing directory, ensure the service user can read it.

## Troubleshooting

- **File not found**: Check that the Markdown file path is correct and the application user has read permissions
- **No columns displayed**: Verify the Markdown file uses `##` for column headers and `- [ ]` for cards
- **Traefik not routing**: Check Traefik logs and ensure the service is running on port 5000

## License

MIT

