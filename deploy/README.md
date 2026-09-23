# Deploying Astrolabe

Astrolabe runs at **https://astrolabe.ryanjrusson.com** on the same EC2
instance as norhog (Ubuntu 24.04, us-west-2), behind the same Caddy, deployed
the same way: CI builds a bundle, puts it in S3, and asks SSM to run
`~/bin/deploy-astrolabe.sh` on the instance. Nothing connects to the server
from outside, and the server stores no user data -- sites and history live in
each visitor's browser.

| On the server | What |
| --- | --- |
| `~/services/astrolabe/` | the current release (Python source, `web/dist`) |
| `~/venvs/astrolabe/` | its virtualenv, outside the release so deploys keep it |
| `~/config/astrolabe/` | `skybrightness.tif` and `skybrightness_tiles/` -- never in git or a bundle |
| `~/.cache/astro-night-planner/` | DE440s ephemeris and the parsed catalogue |
| `/var/www/astrolabe/` | the built web app, which Caddy serves (it cannot read `/home/ubuntu`) |
| `astrolabe.service` | uvicorn on `127.0.0.1:8001`, one worker |

| File here | Installed as |
| --- | --- |
| `astrolabe.service` | `/etc/systemd/system/astrolabe.service` |
| `Caddyfile` | appended to `/etc/caddy/Caddyfile` |
| `deploy-astrolabe.sh` | `~/bin/deploy-astrolabe.sh` |
| `build-bundle.sh` | run by CI (or by hand) to make the bundle |

## One-time setup

### In AWS (console)

1. **Resize to t3.micro.** norhog alone leaves about 125 MB free on the
   t3.nano. EC2 → the instance → *Instance state → Stop*; once stopped,
   *Actions → Instance settings → Change instance type* → `t3.micro`;
   *Instance state → Start*. The Elastic IP stays attached.
2. **DNS.** `ryanjrusson.com` is on Cloudflare: *DNS → Records → Add record*,
   type `A`, name `astrolabe`, IPv4 the instance's Elastic IP -- the same
   address norhog's record uses -- TTL Auto, and **Proxy status off** ("DNS
   only", grey cloud). Caddy gets the certificate itself, so browsers should
   reach it directly; behind Cloudflare's proxy there would be a second TLS
   layer, and Cloudflare's default SSL mode loops against Caddy's redirect.
   To proxy it later, set SSL/TLS to *Full (strict)* first.
   `nslookup astrolabe.ryanjrusson.com` should then answer with the Elastic
   IP, not a Cloudflare address.
3. **Let the new repository use norhog's deploy role.** IAM → Roles → the role
   norhog's `AWS_DEPLOY_ROLE_ARN` names → *Trust relationships → Edit*. Where
   the condition on `token.actions.githubusercontent.com:sub` lists
   `repo:RyGuy907/norhog:...`, make it a list and add
   `repo:RyGuy907/astrolabe:ref:refs/heads/main`. Its permissions already
   cover uploading to the deploy bucket and `ssm:SendCommand` to this
   instance; if the S3 permission names a key prefix rather than `bucket/*`,
   add `arn:aws:s3:::<bucket>/astrolabe/*`.
4. **Upload the sky-brightness map** to the deploy bucket: S3 → the bucket in
   norhog's `DEPLOY_BUCKET` → *Create folder* `astrolabe` → upload
   `data/skybrightness-bundle.tar.gz` into it (made by the command under
   *Updating the sky-brightness map*, below).

### In GitHub (repository settings → Secrets and variables → Actions)

- Secret `AWS_DEPLOY_ROLE_ARN`: the role from step 3.
- Variable `DEPLOY_BUCKET`: the same bucket as norhog's.
- Variable `INSTANCE_ID`: the instance's `i-...` id.

The deploy job is skipped until `INSTANCE_ID` is set.

### On the server (Session Manager)

EC2 → the instance → *Connect → Session Manager → Connect*. Sessions start as
`ssm-user`; everything below runs as `ubuntu`:

```sh
sudo su - ubuntu
```

Check the box: memory after the resize, disk, that port 8001 is free (no
output), and that the tools are there.

```sh
free -m
df -h /
ss -ltn | grep ':8001 '
command -v aws rsync curl caddy
```

Python's venv module, then the directories and the virtualenv:

```sh
sudo apt-get update
sudo apt-get install -y python3.12-venv
mkdir -p ~/services ~/config/astrolabe ~/bin
sudo install -d -o ubuntu -g ubuntu -m 755 /var/www/astrolabe
python3 -m venv ~/venvs/astrolabe
~/venvs/astrolabe/bin/pip install --upgrade pip
```

The sky-brightness map, from S3 (replace `BUCKET`):

```sh
aws s3 cp s3://BUCKET/astrolabe/skybrightness-bundle.tar.gz /tmp/
tar -xzf /tmp/skybrightness-bundle.tar.gz -C ~/config/astrolabe
rm /tmp/skybrightness-bundle.tar.gz
ls -la ~/config/astrolabe ~/config/astrolabe/skybrightness_tiles/meta.json
```

The deploy script and the service, from the repository:

```sh
RAW=https://raw.githubusercontent.com/RyGuy907/astrolabe/main/deploy
curl -fsSL "$RAW/deploy-astrolabe.sh" -o ~/bin/deploy-astrolabe.sh
chmod +x ~/bin/deploy-astrolabe.sh
curl -fsSL "$RAW/astrolabe.service" | sudo tee /etc/systemd/system/astrolabe.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable astrolabe
```

(`enable` without `--now`: there is no release to run yet. The first deploy
starts it.)

Caddy. Back up the Caddyfile, append Astrolabe's block, and **validate before
reloading** -- a broken Caddyfile would take norhog down too:

```sh
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date +%F)
curl -fsSL "$RAW/Caddyfile" | sudo tee -a /etc/caddy/Caddyfile >/dev/null
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

If `validate` fails, put the backup back
(`sudo cp /etc/caddy/Caddyfile.bak-$(date +%F) /etc/caddy/Caddyfile`) and do
not reload. Until the first deploy the site answers with an empty page; the
certificate is issued as soon as the DNS record resolves.

### The first deploy

Push to `main` (or *Actions → CI → Run workflow* on `main` once the settings
are in). Then, on the server, fetch the ephemeris once so the first visitor
does not wait for 32 MB from JPL:

```sh
cd ~/services/astrolabe && ~/venvs/astrolabe/bin/python -c "from engine.ephem import load_ephemeris; load_ephemeris()"
```

### Check it

```sh
systemctl status astrolabe --no-pager
curl -s http://127.0.0.1:8001/api/health
curl -sI https://astrolabe.ryanjrusson.com | head -5
free -m
pm2 status
```

`/api/health` should say `"ephemeris_cached": true`. Then reboot once
(`sudo reboot`) and check that both sites come back by themselves. On a phone:
*Use my location* (needs HTTPS, so it only works here), pinching the map,
touching the altitude chart, and *Share → Add to Home Screen*.

## Every deploy after that

Push to `main`. CI runs the tests, and if they pass builds the bundle, uploads
it, runs the deploy on the instance (which rolls back if the API does not come
up), and checks the live site. The Actions log shows the server's output.

By hand, without CI (replace `BUCKET`): locally,

```sh
deploy/build-bundle.sh
aws s3 cp astrolabe.tar.gz s3://BUCKET/astrolabe/manual.tar.gz
```

(or upload it in the S3 console), then on the server as `ubuntu`:

```sh
~/bin/deploy-astrolabe.sh s3://BUCKET/astrolabe/manual.tar.gz
```

## When these files change

`deploy-astrolabe.sh`, `astrolabe.service` and `Caddyfile` are copies on the
server; a push does not update them. Re-run the matching `curl` above
(`sudo systemctl daemon-reload && sudo systemctl restart astrolabe` after the
unit; for the Caddyfile, replace the block by hand, then validate and reload).

## Updating the sky-brightness map

After `scripts/build_skyglow.py apply`, `tiles` and `install`, pack it on the
development machine:

```sh
tar -czf data/skybrightness-bundle.tar.gz -C config skybrightness.tif skybrightness_tiles
```

Upload it to `s3://BUCKET/astrolabe/`, repeat the three S3 commands above on
the server, and `sudo systemctl restart astrolabe`. The tile URLs carry the
map's version, so browsers pick up the new overlay at once.

## Logs

```sh
journalctl -u astrolabe -n 100 --no-pager
journalctl -u caddy -n 50 --no-pager
```
