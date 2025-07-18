# OpenThread CLI - Border Router (BR)

## Command List

Usage : `br [command] ...`

- [counters](#counters)
- [disable](#disable)
- [enable](#enable)
- [help](#help)
- [init](#init)
- [nat64prefix](#nat64prefix)
- [omrprefix](#omrprefix)
- [onlinkprefix](#onlinkprefix)
- [pd](#pd)
- [peers](#peers)
- [prefixtable](#prefixtable)
- [rioprf](#rioprf)
- [routeprf](#routeprf)
- [routers](#routers)
- [state](#state)

## Command Details

### help

Usage: `br help`

Print BR command help menu.

```bash
> br help
counters
disable
enable
multiail
omrconfig
omrprefix
onlinkprefix
pd
peers
prefixtable
raoptions
rdnsstable
rioprf
routeprf
routers
state
Done
```

### init

Usage: `br init <interface> <enabled>`

Initializes the Border Routing Manager on given infrastructure interface.

```bash
> br init 2 1
Done
```

### infraif

Usage: `br infraif`

Get the interface index and running state of the configured infrastructure interface.

```bash
> br infraif
if-index:2, is-running:yes
Done
```

### enable

Usage: `br enable`

Enable the Border Routing functionality.

```bash
> br enable
Done
```

### disable

Usage: `br disable`

Disable the Border Routing functionality.

```bash
> br disable
Done
```

### state

Usage: `br state`

Get the Border Routing state:

- `uninitialized`: Routing Manager is uninitialized.
- `disabled`: Routing Manager is initialized but disabled.
- `stopped`: Routing Manager in initialized and enabled but currently stopped.
- `running`: Routing Manager is initialized, enabled, and running.

```bash
> br state
running
```

### counters

Usage : `br counters`

Get the Border Router counter.

```bash
> br counters
Inbound Unicast: Packets 4 Bytes 320
Inbound Multicast: Packets 0 Bytes 0
Outbound Unicast: Packets 2 Bytes 160
Outbound Multicast: Packets 0 Bytes 0
RA Rx: 4
RA TxSuccess: 2
RA TxFailed: 0
RS Rx: 0
RS TxSuccess: 2
RS TxFailed: 0
Done
```

### multiail

Usage : `br multiail`

Requires `OPENTHREAD_CONFIG_BORDER_ROUTING_MULTI_AIL_DETECTION_ENABLE`.

Get the current detected state regarding multiple Adjacent Infrastructure Links (AILs) indicating whether the Routing Manager currently believes that Border Routers (BRs) on the Thread mesh may be connected to different AILs.

The detection mechanism operates as follows: The Routing Manager monitors the number of peer BRs listed in the Thread Network Data (see `br peers`) and compares this count with the number of peer BRs discovered by processing received Router Advertisement (RA) messages on its connected AIL. If the count derived from Network Data consistently exceeds the count derived from RAs for a detection duration of 10 minutes, it concludes that BRs are likely connected to different AILs. To clear the state a shorter window of 1 minute is used.

The detection window of 10 minutes helps to avoid false positives due to transient changes. The Routing Manager uses 200 seconds for reachability checks of peer BRs (sending Neighbor Solicitation). Stale Network Data entries are also expected to age out within a few minutes. So a 10-minute detection time accommodates both cases.

While generally effective, this detection mechanism may get less reliable in scenarios with a large number of BRs, particularly exceeding ten. This is related to the "Network Data Publisher" mechanism, where BRs might refrain from publishing their external route information in the Network Data to conserve its limited size, potentially skewing the Network Data BR count.

```bash
> br multiail
not detected
Done

> br multiail
detected
Done
```

Usage: `br multiail callback enable|disable`

Enable or disable callback to be notified of changes in the multi-AIL detection state.

```bash
> br multiail callback enable
Done

BR multi AIL callback: detected

> br multiail
detected
Done

BR multi AIL callback: cleared
```

### omrconfig

Usage: `br omrconfig`

Get the current OMR prefix configuration mode.

The possible modes are:

- `auto`: BR auto-generates the local OMR prefix.
- `custom`: BR uses a given custom OMR prefix with its associated preference.
- `disabled`: BR does not add local/PD OMR prefix in Network Data.

```bash
> br omrconfig
auto
Done
```

Usage: `br omrconfig auto`

Set the current OMR prefix configuration mode to `auto`.

```
> br omrconfig auto
Done

> br omrconfig
auto
Done
```

Usage: `br omrconfig custom <prefix> [high|med|low]`

Set the current OMR prefix configuration mode to `custom`

```
> br omrconfig custom fd00::/64 med
Done

> br omrconfig
custom (fd00:0:0:0::/64, prf:med)
Done
```

Usage: `br omrconfig disable`

Set the current OMR prefix configuration mode to `disabled`

```
> br omrconfig disable
Done

> br omrconfig
disabled
Done
```

### omrprefix

Usage: `br omrprefix [local|favored]`

Get local or favored or both off-mesh-routable prefixes of the Border Router.

```bash
> br omrprefix
Local: fdfc:1ff5:1512:5622::/64
Favored: fdfc:1ff5:1512:5622::/64 prf:low
Done

> br omrprefix favored
fdfc:1ff5:1512:5622::/64 prf:low
Done

> br omrprefix local
fdfc:1ff5:1512:5622::/64
Done
```

### onlinkprefix

Usage: `br onlinkprefix [local|favored]`

Get local or favored or both on-link prefixes of the Border Router.

```bash
> br onlinkprefix
Local: fd41:2650:a6f5:0::/64
Favored: 2600::0:1234:da12::/64
Done

> br onlinkprefix favored
2600::0:1234:da12::/64
Done

> br onlinkprefix local
fd41:2650:a6f5:0::/64
Done
```

### nat64prefix

Usage: `br nat64prefix [local|favored]`

Get local or favored or both NAT64 prefixes of the Border Router.

`OPENTHREAD_CONFIG_NAT64_BORDER_ROUTING_ENABLE` is required.

```bash
> br nat64prefix
Local: fd14:1078:b3d5:b0b0:0:0::/96
Favored: fd14:1078:b3d5:b0b0:0:0::/96 prf:low
Done

> br nat64prefix favored
fd14:1078:b3d5:b0b0:0:0::/96 prf:low
Done

> br nat64prefix
fd14:1078:b3d5:b0b0:0:0::/96
Done
```

### pd

Usage: `br pd [enable|disable]`

Enable/Disable the DHCPv6 PD.

```bash
> br pd enable
Done

> br pd disable
Done
```

Usage: `br pd state`

Get the state of DHCPv6 PD.

`OPENTHREAD_CONFIG_BORDER_ROUTING_DHCP6_PD_ENABLE` is required.

- `disabled`: DHCPv6 PD is disabled on the border router.
- `stopped`: DHCPv6 PD in enabled but won't try to request and publish a prefix.
- `running`: DHCPv6 PD is enabled and will try to request and publish a prefix.

```bash
> br pd state
running
Done
```

Usage `br pd omrprefix`

Get the DHCPv6 Prefix Delegation (PD) provided off-mesh-routable (OMR) prefix.

`OPENTHREAD_CONFIG_BORDER_ROUTING_DHCP6_PD_ENABLE` is required.

```bash
> br pd omrprefix
2001:db8:cafe:0:0/64 lifetime:1800 preferred:1800
Done
```

### peers

Usage: `br peers`

Get the list of peer BRs found in the Network Data.

`OPENTHREAD_CONFIG_BORDER_ROUTING_TRACK_PEER_BR_INFO_ENABLE` is required.

Peer BRs are other devices within the Thread mesh that provide external IP connectivity. A device is considered to provide external IP connectivity if at least one of the following conditions is met regarding its Network Data entries:

- It has added at least one external route entry.
- It has added at least one prefix entry with both the default-route and on-mesh flags set.
- It has added at least one domain prefix (with both the domain and on-mesh flags set).

The list of peer BRs specifically excludes the current device, even if it is itself acting as a BR.

Info per BR entry:

- RLOC16 of the BR
- Age as the duration interval since this BR appeared in Network Data. It is formatted as `{hh}:{mm}:{ss}` for hours, minutes, seconds, if the duration is less than 24 hours. If the duration is 24 hours or more, the format is `{dd}d.{hh}:{mm}:{ss}` for days, hours, minutes, seconds.

```bash
> br peers
rloc16:0x5c00 age:00:00:49
rloc16:0xf800 age:00:01:51
Done
```

Usage: `br peers count`

Gets the number of peer BRs found in the Network Data.

The count does not include the current device, even if it is itself acting as a BR.

The output indicates the minimum age among all peer BRs. Age is formatted as `{hh}:{mm}:{ss}` for hours, minutes, seconds, if the duration is less than 24 hours. If the duration is 24 hours or more, the format is `{dd}d.{hh}:{mm}:{ss}` for days, hours, minutes, seconds.

```bash
> br peer count
2 min-age:00:00:49
Done
```

### prefixtable

Usage: `br prefixtable`

Get the discovered prefixes by Border Routing Manager on the infrastructure link.

Info per prefix entry:

- The prefix
- Whether the prefix is on-link or route
- Milliseconds since last received Router Advertisement containing this prefix
- Prefix lifetime in seconds
- Preferred lifetime in seconds only if prefix is on-link
- Route preference (low, med, high) only if prefix is route (not on-link)
- The router IPv6 address which advertising this prefix
- Flags in received Router Advertisement header:
  - M: Managed Address Config flag
  - O: Other Config flag
  - S: SNAC Router flag

```bash
> br prefixtable
prefix:fd00:1234:5678:0::/64, on-link:no, ms-since-rx:29526, lifetime:1800, route-prf:med, router:ff02:0:0:0:0:0:0:1 (M:0 O:0 S:1)
prefix:1200:abba:baba:0::/64, on-link:yes, ms-since-rx:29527, lifetime:1800, preferred:1800, router:ff02:0:0:0:0:0:0:1 (M:0 O:0 S:1)
Done
```

### raoptions

Usage: `br raoptions <options>`

Sets additional options to append at the end of emitted Router Advertisement (RA) messages. `<options>` provided as hex bytes.

```bash
> br raoptions 0400ff00020001
Done
```

### raoptions clear

Usage: `br raoptions clear`

Clear any previously set additional options to append at the end of emitted Router Advertisement (RA) messages.

```bash
> br raoptions clear
Done
```

### rdnsstable

Usage: `br rdnsstable`

Get the discovered Recursive DNS Server (RDNSS) address table by Border Routing Manager on the infrastructure link.

Info per entry:

- IPv6 address
- Lifetime in seconds
- Milliseconds since last received Router Advertisement containing this address
- The router IPv6 address which advertised this prefix
- Flags in received Router Advertisement header:
  - M: Managed Address Config flag
  - O: Other Config flag
  - S: SNAC Router flag

```bash
> br rdnsstable
fd00:1234:5678::1, lifetime:500, ms-since-rx:29526, router:ff02:0:0:0:0:0:0:1 (M:0 O:0 S:1)
fd00:aaaa::2, lifetime:500, ms-since-rx:107, router:ff02:0:0:0:0:0:0:1 (M:0 O:0 S:1)
Done
```

### rioprf

Usage: `br rioprf`

Get the preference used when advertising Route Info Options (e.g., for discovered OMR prefixes) in emitted Router Advertisement message.

```bash
> br rioprf
med
Done
```

### rioprf \<prf\>

Usage: `br rioprf high|med|low`

Set the preference (which may be 'high', 'med', or 'low') to use when advertising Route Info Options (e.g., for discovered OMR prefixes) in emitted Router Advertisement message.

```bash
> br rioprf low
Done
```

### rioprf clear

Usage: `br rioprf clear`

Clear a previously set preference value for advertising Route Info Options (e.g., for discovered OMR prefixes) in emitted Router Advertisement message. When cleared BR will use device's role to determine the RIO preference: Medium preference when in router/leader role and low preference when in child role.

```bash
> br rioprf clear
Done
```

### routeprf

Usage: `br routeprf`

Get the preference used for publishing routes in Thread Network Data. This may be the automatically determined route preference, or an administratively set fixed route preference - if applicable.

```bash
> br routeprf
med
Done
```

### routeprf \<prf\>

Usage: `br routeprf high|med|low`

Set the preference (which may be 'high', 'med', or 'low') to use publishing routes in Thread Network Data. Setting a preference value overrides the automatic route preference determination. It is used only for an explicit administrative configuration of a Border Router.

```bash
> br routeprf low
Done
```

### routeprf clear

Usage: `br routeprf clear`

Clear a previously set preference value for publishing routes in Thread Network Data. When cleared BR will automatically determine the route preference based on device's role and link quality to parent (when acting as end-device).

```bash
> br routeprf clear
Done
```

### routers

Usage: `br routers`

Get the list of discovered routers by Border Routing Manager on the infrastructure link.

Info per router:

- The router IPv6 address
- Flags in received Router Advertisement header:
  - M: Managed Address Config flag
  - O: Other Config flag
  - S: SNAC Router flag (indicates whether the router is a stub router)
- Milliseconds since last received message from this router
- Reachability flag: A router is marked as unreachable if it fails to respond to multiple Neighbor Solicitation probes.
- Age: Duration interval since this router was first discovered. It is formatted as `{hh}:{mm}:{ss}` for hours, minutes, seconds, if the duration is less than 24 hours. If the duration is 24 hours or more, the format is `{dd}d.{hh}:{mm}:{ss}` for days, hours, minutes, seconds.
- `(this BR)` is appended when the router is the local device itself.
- `(peer BR)` is appended when the router is likely a peer BR connected to the same Thread mesh. This requires `OPENTHREAD_CONFIG_BORDER_ROUTING_TRACK_PEER_BR_INFO_ENABLE`.

```bash
> br routers
ff02:0:0:0:0:0:0:1 (M:0 O:0 S:1) ms-since-rx:1505 reachable:yes age:00:18:13
Done
```
