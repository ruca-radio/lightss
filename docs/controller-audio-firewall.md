# Controller audio delivery on this computer

Live delivery was verified on 2026-10-11 after the user moved both controllers
from `DeFi-Aux5` to the computer's Wi-Fi network, `Hotspot & Chill`. The GUI
receives the left controller's microphone audio from `10.27.27.110` through
multicast `239.0.0.1:11988` on `wlo1`. Eight half-second API probes all reported
`active=true`; `receive_sequence` advanced from `4199` to `4344`, with changing,
nonzero levels and FFT values. Both controllers remained reachable over HTTP;
the left reports microphone/send mode and the right reports receive mode.

Desktop (1440x1080) and mobile (390x844) browser checks used the real audio
stream, without injected frames. Both displayed `Controller mic · live audio`,
an advancing packet counter, changing level-history and spectrum canvases,
and nonzero values. Neither had JavaScript exceptions or horizontal overflow.

Before the Wi-Fi change, `/api/audio` reported `active=false` and
`receive_sequence=null`; an independent listener and an eight-second capture
also received no audio packets. Host checks found UFW inactive and no incoming
host audio filtering in the inspected nftables rules. No firewall change was
needed to restore delivery. The successful same-SSID test points to the prior
SSID boundary; the access point's exact forwarding policy has not been inspected.

For a packet-delivery check:

```sh
sudo timeout 8 tcpdump -n -i wlo1 'udp port 11988 or igmp'
```

Verify `/api/audio` on the running GUI at `http://127.0.0.1:8123`: `active` should
be true and `receive_sequence` should advance. Nonzero changing level and FFT
values verify sound, beyond packet delivery. The UI keeps an unavailable
audio message visible, and spectrum clicks report applied/rejected writes
inside the visible panel. Synthetic UI tests alone do not prove live reception;
the real API and rendered checks above establish delivery for this network setup.

If UFW is enabled in the future and evidence shows it blocks the packets, an
administrator can allow only the verified sender's multicast traffic:

```sh
sudo ufw allow in on wlo1 proto udp from 10.27.27.110 to 239.0.0.1 port 11988 comment 'Lightss controller audio'
```

This rule has not been applied. Recheck the sender and interface before using
it; it is unnecessary while UFW is inactive.

To remove this exact rule:

```sh
sudo ufw delete allow in on wlo1 proto udp from 10.27.27.110 to 239.0.0.1 port 11988
```
