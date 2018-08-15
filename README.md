# Attendedsysupgrade Server for OpenWrt (GSoC 2017)

This project intends to simplify the sysupgrade process of devices running
OpenWrt or distributions based on the former like LibreMesh. The provided tools
here offer an easy way to reflash the router with a new version or package
upgrades, without the need of `opkg` installed.

Additionally it offers an API (covered below) to request custom images with any
selection of packages pre-installed, allowing to create firmware images without
the need of setting up a build environment, even from mobile devices.

## Clients

### [`luci-app-attendedsysupgrade`](https://github.com/openwrt/luci/tree/master/applications/luci-app-attendedsysupgrade)

Add a view to the Luci system tab called "Attended Sysupgrade". Offers a button to search for updates and if found, to flash the image created by the update server.

**Dependencies:**
* `rpcd-mod-rpcsys`
	Used to read list of installed packages and trigger sysupgrade on the target.
* `uhttpd-mod-ubus`
	Communication between the Browser and the Router
* `cgi-io`
	Upload image from Browser to Router
	
![luci-app-attendedsysupgrade-screenshot](https://camo.githubusercontent.com/d21d3c2e43993325c0371866b28f09a67ea21902/687474703a2f2f692e696d6775722e636f6d2f653443716841502e706e67)

### [`auc`](https://github.com/openwrt/packages/tree/master/utils/auc)

Add CLI to perform sysupgrades.

**Dependencies:**
* `rpcd-mod-rpcsys`
	Used to read list of installed packages and trigger sysupgrade on the target.
* `usteam-ssl` and `ca-certificates`
	Securely communicate and download firmware from server via https

### [Chef Online Builder](https://github.com/libremesh/chef)

* https://as-test.stephen304.com/chef **dev version**
* https://chef.libremesh.org

![Chef](https://screenshotscdn.firefoxusercontent.com/images/6f96a6e3-1999-47b8-a3c7-643b61b56a0c.png)

## Server

The server listens to update and image requests and images are automatically
generated if the requests was valid. This is done by automatically setting up
OpenWrt ImageBuilders and cache images in a database. This allows to quickly
respond to requests without rebuilding exiting images again.

### Active server

* [stephen304](https://as-test.stephen304.com/) **dev version**
* [planetexpress](https://ledeupdate.planetexpress.cc)

You can set this server in `/etc/config/attendedsysupgrade` after installation
of a client.

## Run your own server

It's easy to run your own upgrade server! You can test it locally via Vagrant or
setup a remote host with Ansible. The following information except you are
familiar with the concept of Vagrant and/or Ansible.

### Locally with Vagrant

Run `vagrant up`, it will automatically run the Ansible playbook and configure
the server. Afterwards login via `vagrant ssh` and start the server via the
following command:

    python3 server

This spins up the following processes:

* The server itself, reachable on `localhost:5000`.
* A garbage collector which automatically removes outdated firmware images.
* An updater, which manages update workers. It downloads all (!) activated.
  ImageBuilders and parse supported profiles and available packages.
* A boss which manages build workers. Currently only local support, eventually
  also via SSH.

### Remote via Ansible

Create a hosts file and run `ansible-playbook site.yml -i hosts` to setup a
server remotely. This also installs Nginx and serves unencrypted on port 80.
Currently there is no `systemd` service file so run the `python3 server` in a
`tmux` or `screen` session.

## API

### Upgrade check `/api/upgrade-check`

Sends information about the device to the server to see if a new distribution
version or package upgrades are available. An *upgrade check* could look like
this:

| key	| value | information	|
| ---	| ---	| ---		|
| `distro` | `LEDE` | installed distribution |
| `version` | `17.01.0` | installed version |
| `target` | `ar71xx` | |
| `subtarget` | `generic` | |
| `packages` | `{libuci-lua: 2017-04-12-c4df32b3-1, cgi-io: 3, ...}` | all user installed packages |

Most information can be retrieved via `ubus call system board`. Missing
information can be gathered via the `rpcd-mod-rpcsys` package. `packages`
contains all user installed packages plus version. Packages installed as an
dependence are excluded as they've been automatically and dependencies may
change between versions.

It's also possible to check for a new version without sending packages by
removing `packages` from the request.

### Response `status 200`

The server validates the request. Below is a possible response for a new
version:

| key		| value		| information	|
| ---		| ---		| ---		|
| `version`		| `17.01.2`		| newest version |
| `upgrades`	| `{luci-lib-jsonc: [git-17.230.25723-2163284-1, git-17.228.56579-209deb5-1], ...}` | Package updates `[new_version, current_version]` |
| `packages`	| `[libuci-lua, cgi-io: 3, ...]` | All packages for the new image |

See [other status codes](#response-status-codes)

An version upgrade does not ignore package upgrades for the following reason.
Between versions it possible that package names may change, packages are dropped
or merged. The *response* contains all packages included changed ones.

The *upgrade check response* should be shown to the user in a readable way.

### Upgrade request `/api/upgrade-request`

Once the user decides to perform the sysupgrade a new request is send to the
server called *upgrade request*.

#### POST

| key		| value					| information	|
| ---		| ---					| ---		|
| `distro`	| `LEDE`				| installed distribution |
| `version`	| `17.01.2`					| installed version |
| `target`	| `ar71xx`				| |
| `subtarget`	| `generic`					| |
| `board`	| `tl-wdr4300-v1`			| `board_name` of `ubus call system board` |
| `[model]`		| `TP-Link TL-WDR4300 v1`		| `model` of `ubus call system board`. This is optional and a fallback |
| `packages`	| `[libuci-lua, cgi-io: 3, ...]`	| All packages for the new image |

The *upgrade request* is nearly the same as the *upgrade check* before, except
only containing package names without version and adding `board` and possibly
`model`. While the server builds the requested image the clients keeps polling
the server sending a `request_hash` via `GET` to the server.

#### GET

If the `request_hash` was retrieved the client should switch to `GET` requests
with the hash to save the server from validating the request again.

`api/upgrade-request/<request_hash`

### Response `status 200`

| key	| value | information	|
| ---	| ---	| ---		|
| `sysupgrade` | `https://betaupdate.libremesh.o…x86-64-Generic-sysupgrade.bin` | download link |
| `image_hash` | `27439cbc07fae59` | Hash of image parameters, like distribution, profile, packages and versions |

See [other status codes](#response-status-codes)

### Build request `/api/build-request`

It's also possible to request to build an image. The request is nearly the same
as for `upgrade-request`. The response only contains a link to the created
`files` or `upgrade-request` parameters if available.

An additional parameter is the `defaults` parameter which allows to set the
content of `/etc/uci-defaults/99-server-defaults` within the image. This allows
to set custom options for the resulting image. To distinguish between custom
images the name will contain a hash of the requested `defaults` value and is
stored in a different place, only visible if the full hash (32bit) is known.

This is a special case for clients that do not necessary require a sysupgrade
compatible image. An example is the [LibreMesh Chef](https://chef.libremesh.org)
firmware builder.

### Response status codes

The client should check the status code:

| status	| meaning												| information	|
| ---		| ---								| ---			|
| 200		| build finish / upgrade available	| see parameters of `upgrade-check`, `upgrade-request` or `build-request` |
| 202		| building, queued, imagebuilder setup	| building right now, in build queue, imagebuilder not ready. Details are in header `X-Imagebuilder-Status` and `X-Build-Queue-Position` |
| 204		| no updates						| device is up to date. Contains `request_hash` |
| 400		| bad request 						| see `error` parameter |
| 413		| imagesize fail					| produced image too big for device |
| 420		| defaults size fail				| requested defaults exceeds maximum size (10kB) |
| 422		| unknown package					| unknown package in request |
| 500		| build failed						| see `log` for build log	|
| 501		| no sysupgrade						| image build successful but no sysupgrade image created |
| 502		| proxy backend down				| nginx runs but python part is down, likely maintenance |
| 503		| server overload					| please wait ~5 minutes |

### Request data

It's also possible to receive information about build images or package
versions, available devices and more. All responses are in `JSON` format.

* `/api/image/<image_hash>` Get information about an image. This contains
  various information stored about the image.

* `/api/manifest/<manifest_hash>` Get packages and versions of a manifest. The
  manifest contains all installed packages of an image. The `manifest_hash` can
  be received by the api call `/api/image`.

* `/api/distros` Get all supported distros with latest version and a short
  description if available.

* `/api/versions[?distro=<distribution>]` Get all supported versions with short
  description (of a singele distribution if given).

* `/api/models?distro=&version=&model_search=<search string>` Get all supported
  devices of distro/version that contain the `model_search` string

* `/api/packages_image?distro=&version=&target=&subtarget=&profile=` Get all
  default packages installed on an image
