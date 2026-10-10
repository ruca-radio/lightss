# Controller audio delivery on this computer

After the python-wled integration and GUI restart, both WLED HTTP/WebSocket
connections work. The left controller reports a working microphone and audio
sync send mode. The GUI receives no multicast audio packets during repeated
live probes, despite correct membership, interface and UDP binding.

UFW is active on this computer. Its actual rules could not be inspected because
this execution session cannot authenticate sudo. A firewall block remains a
hypothesis until the rule below is applied and packet reception is checked.

An administrator can allow only the verified sender's audio multicast traffic:

```sh
sudo ufw allow in on wlo1 proto udp from 10.27.27.110 to 239.0.0.1 port 11988 comment 'Lightss controller audio'
```

The installed UFW parser accepted this rule. It has not been applied. It does
not open general LAN/internet UDP access or modify a controller/router.

Verify `/api/audio` on the running GUI at `http://127.0.0.1:8123`: `active` should
be true and `receive_sequence` should advance. Refresh the UI to load the meter
fixes. If packets remain absent, investigate multicast delivery before relaxing
firewall scope or changing controller settings.

To remove this exact rule:

```sh
sudo ufw delete allow in on wlo1 proto udp from 10.27.27.110 to 239.0.0.1 port 11988
```
