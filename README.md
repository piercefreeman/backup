# backup

A collection of scripts to backup local and cloud services. Intended for personal use but feel free to fork for your own needs. When backing up cloud services it's usually more optimal to launch a cheap VM and run the backup logic there, since we cut back on cloud->residential bandwidth just to push it back in the cloud.

Current support for:
- iCloud Photo

Planned support for:
- iCloud Storage
- Google Drive

Environment variables are expected to be set in a `.env` file in the root of the project or somewhere on the path to root. See `.env.sample` for an example that can be cloned into a populated `.env` file.

## Getting Started

There's one CLI command, that's it. Run it and it will backup all services.

```
poetry run backup
```
