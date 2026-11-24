# Where does your app live?

Paste a domain. See who owns the servers behind it, and whether it's really
European.

"Our servers are in Stockholm" and "our data is in Europe" are not the same
thing. A US company falls under the US CLOUD Act wherever its servers stand,
and a CDN in front of your app decrypts every request on its own network.
This app follows a domain through DNS to the networks that actually serve it
and tells you who owns them.

## What it checks

1. **DNS:** the CNAME chain and the public IPv4 and IPv6 addresses. The chain
   often names the platform outright (`*.cloudfront.net`, `*.vercel-dns.com`,
   `*.azurefd.net`, `*.eu-north-1.elb.amazonaws.com`).
2. **Network owner:** each address is looked up in
   [RIPEstat](https://stat.ripe.net/docs/data-api/): which network (ASN)
   announces it, who holds that network and where it is registered.
3. **Parent company:** a curated table maps the networks that matter to the
   company behind them. Registration country alone misleads: Akamai registers
   networks in the Netherlands and Cloudflare in London, but both are US
   companies. Unknown networks fall back to the registration country, and the
   verdict says so.
4. **Region:** addresses are matched against the IP ranges AWS and Google
   Cloud publish, so "AWS" becomes "AWS eu-north-1 (Stockholm)".
5. **Verdict:**

   | Verdict | Meaning |
   |---|---|
   | European | Every company we can see in the path is based in Europe |
   | US front door | A US-owned CDN terminates HTTPS in front; the origin is hidden |
   | US-owned | US-owned infrastructure serves it, whatever the region |
   | Outside Europe | Owned by a company outside Europe and the US |
   | Unknown | No public addresses, or owners we can't place |

Ownership decides the verdict, not IP geolocation. CDNs use anycast, so their
addresses are "everywhere", and a location says nothing about who can be
compelled to hand over data.

This is a best-effort technical check, not legal advice. Anything behind a
CDN is invisible from the outside, and the verdict says so instead of
guessing.

## Run it

Python 3.12 or later.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m app            # http://localhost:8000, or set PORT
```

The app is a plain ASGI app (`app.main:app`) that listens on `$PORT`
(default 8000), needs no secrets and no database, and answers
`GET /healthz` for health checks. It makes outbound HTTPS calls to
stat.ripe.net, ip-ranges.amazonaws.com and www.gstatic.com, and DNS lookups.

## API

```bash
curl "http://localhost:8000/api/lookup?domain=example.com"
```

Takes a domain, a URL or an IP address. Returns the verdict, the CNAME chain,
the platforms recognised in it and one entry per address with owner, ASN,
country and region. Results are cached for five minutes, RIPEstat answers for
hours. Interactive docs at `/api/docs`.

## Tests

```bash
.venv/bin/pytest                         # offline
.venv/bin/python scripts/check_owners.py # verify the owner table against RIPEstat
```

## Roadmap

- **Deep scan:** fetch the page and check every third-party host it loads
  (fonts, analytics, scripts, CDNs). A "European" site often pulls half of
  itself from the US. This runs as a background job: a queue, workers that
  scale out, and Postgres for the results. Fetching user-supplied URLs needs
  an SSRF guard first (public addresses only, no redirects into private
  ranges, size and time limits).
- **Shared cache:** Redis behind the same interface as the in-process cache,
  so every replica shares one set of lookups.

## Data and credits

- [RIPEstat Data API](https://stat.ripe.net/docs/data-api/) by the RIPE NCC
- GeoLite2 data created by [MaxMind](https://www.maxmind.com), via RIPEstat
- [AWS IP address ranges](https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html)
  and [Google Cloud IP ranges](https://www.gstatic.com/ipranges/cloud.json)

## License

MIT
