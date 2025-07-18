#!/usr/bin/env python3
#
#  Copyright (c) 2016, The OpenThread Authors.
#  All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#  1. Redistributions of source code must retain the above copyright
#     notice, this list of conditions and the following disclaimer.
#  2. Redistributions in binary form must reproduce the above copyright
#     notice, this list of conditions and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#  3. Neither the name of the copyright holder nor the
#     names of its contributors may be used to endorse or promote products
#     derived from this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
#  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
#  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
#  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
#  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
#  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
#  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
#  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
#  POSSIBILITY OF SUCH DAMAGE.
#

import json
import binascii
import ipaddress
import logging
import os
import re
import shlex
import socket
import subprocess
import sys
import time
import traceback
import typing
import unittest
from ipaddress import IPv6Address, IPv6Network
from typing import Union, Dict, Optional, List, Any

import pexpect
import pexpect.popen_spawn

import config
import simulator
import thread_cert

PORT_OFFSET = int(os.getenv('PORT_OFFSET', "0"))

INFRA_DNS64 = int(os.getenv('NAT64', 0))


class OtbrDocker:
    RESET_DELAY = 3

    _socat_proc = None
    _ot_rcp_proc = None
    _docker_proc = None
    _border_routing_counters = None

    def __init__(self, nodeid: int, backbone_network: str, **kwargs):
        self.verbose = int(float(os.getenv('VERBOSE', 0)))

        assert backbone_network is not None
        self.backbone_network = backbone_network
        try:
            self._docker_name = config.OTBR_DOCKER_NAME_PREFIX + str(nodeid)
            self._prepare_ot_rcp_sim(nodeid)
            self._launch_docker()
        except Exception:
            traceback.print_exc()
            self.destroy()
            raise

    def _prepare_ot_rcp_sim(self, nodeid: int):
        self._socat_proc = subprocess.Popen(['socat', '-d', '-d', 'pty,raw,echo=0', 'pty,raw,echo=0'],
                                            stderr=subprocess.PIPE,
                                            stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL)

        line = self._socat_proc.stderr.readline().decode('ascii').strip()
        self._rcp_device_pty = rcp_device_pty = line[line.index('PTY is /dev') + 7:]
        line = self._socat_proc.stderr.readline().decode('ascii').strip()
        self._rcp_device = rcp_device = line[line.index('PTY is /dev') + 7:]
        logging.info(f"socat running: device PTY: {rcp_device_pty}, device: {rcp_device}")

        ot_rcp_path = self._get_ot_rcp_path()
        self._ot_rcp_proc = subprocess.Popen(f"{ot_rcp_path} {nodeid} > {rcp_device_pty} < {rcp_device_pty}",
                                             shell=True,
                                             stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)

        try:
            self._ot_rcp_proc.wait(1)
        except subprocess.TimeoutExpired:
            # We expect ot-rcp not to quit in 1 second.
            pass
        else:
            raise Exception(f"ot-rcp {nodeid} exited unexpectedly!")

    def _get_ot_rcp_path(self) -> str:
        srcdir = os.environ['top_builddir']
        path = '%s/examples/apps/ncp/ot-rcp' % srcdir
        logging.info("ot-rcp path: %s", path)
        return path

    def _launch_docker(self):
        logging.info(f'Docker image: {config.OTBR_DOCKER_IMAGE}')
        subprocess.check_call(f"docker rm -f {self._docker_name} || true", shell=True)
        CI_ENV = os.getenv('CI_ENV', '').split()
        dns = ['--dns=127.0.0.1'] if INFRA_DNS64 == 1 else ['--dns=8.8.8.8']
        nat64_prefix = ['--nat64-prefix', '2001:db8:1:ffff::/96'] if INFRA_DNS64 == 1 else []
        os.makedirs('/tmp/coverage/', exist_ok=True)

        cmd = ['docker', 'run'] + CI_ENV + [
            '--rm',
            '--name',
            self._docker_name,
            '--network',
            self.backbone_network,
        ] + dns + [
            '-i',
            '--sysctl',
            'net.ipv6.conf.all.disable_ipv6=0 net.ipv4.conf.all.forwarding=1 net.ipv6.conf.all.forwarding=1',
            '--privileged',
            '--cap-add=NET_ADMIN',
            '--volume',
            f'{self._rcp_device}:/dev/ttyUSB0',
            '-v',
            '/tmp/coverage/:/tmp/coverage/',
            config.OTBR_DOCKER_IMAGE,
            '-B',
            config.BACKBONE_IFNAME,
            '--trel-url',
            f'trel://{config.BACKBONE_IFNAME}',
        ] + nat64_prefix
        logging.info(' '.join(cmd))
        self._docker_proc = subprocess.Popen(cmd,
                                             stdin=subprocess.DEVNULL,
                                             stdout=sys.stdout if self.verbose else subprocess.DEVNULL,
                                             stderr=sys.stderr if self.verbose else subprocess.DEVNULL)

        launch_docker_deadline = time.time() + 300
        launch_ok = False

        while time.time() < launch_docker_deadline:
            try:
                subprocess.check_call(f'docker exec -i {self._docker_name} ot-ctl state', shell=True)
                launch_ok = True
                logging.info("OTBR Docker %s on %s Is Ready!", self._docker_name, self.backbone_network)
                break
            except subprocess.CalledProcessError:
                time.sleep(5)
                continue

        assert launch_ok

        self.start_ot_ctl()

    def __repr__(self):
        return f'OtbrDocker<{self.nodeid}>'

    def start_otbr_service(self):
        self.bash('service otbr-agent start')
        self.simulator.go(3)
        self.start_ot_ctl()

    def stop_otbr_service(self):
        self.stop_ot_ctl()
        self.bash('service otbr-agent stop')

    def stop_mdns_service(self):
        self.send_command('mdns disable')
        # OT build may not include mdns, so ignore `InvalidCommand` errors.
        self._expect(r'Done|Error 35: InvalidCommand')
        self.bash('service avahi-daemon stop; service mdns stop; !(cat /proc/net/udp | grep -i :14E9)')

    def start_mdns_service(self):
        self.send_command('mdns enable')
        # OT build may not include mdns, so ignore `InvalidCommand` errors.
        self._expect(r'Done|Error 35: InvalidCommand')
        self.bash('service avahi-daemon start; service mdns start; cat /proc/net/udp | grep -i :14E9')

    def start_ot_ctl(self):
        cmd = f'docker exec -i {self._docker_name} ot-ctl'
        self.pexpect = pexpect.popen_spawn.PopenSpawn(cmd, timeout=30)
        if self.verbose:
            self.pexpect.logfile_read = sys.stdout.buffer

        # Add delay to ensure that the process is ready to receive commands.
        timeout = 0.4
        while timeout > 0:
            self.pexpect.send('\r\n')
            try:
                self.pexpect.expect('> ', timeout=0.1)
                break
            except pexpect.TIMEOUT:
                timeout -= 0.1

    def stop_ot_ctl(self):
        self.pexpect.sendeof()
        self.pexpect.wait()
        self.pexpect.proc.kill()

    def reserve_udp_port(self, port):
        self.bash(f'socat -u UDP6-LISTEN:{port},bindtodevice=wpan0 - &')

    def destroy(self):
        logging.info("Destroying %s", self)
        self._shutdown_docker()
        self._shutdown_ot_rcp()
        self._shutdown_socat()

    def _shutdown_docker(self):
        if self._docker_proc is None:
            return

        try:
            COVERAGE = int(os.getenv('COVERAGE', '0'))
            OTBR_COVERAGE = int(os.getenv('OTBR_COVERAGE', '0'))
            test_name = os.getenv('TEST_NAME')
            unique_node_id = f'{test_name}-{PORT_OFFSET}-{self.nodeid}'

            if COVERAGE or OTBR_COVERAGE:
                self.bash('service otbr-agent stop')

                cov_file_path = f'/tmp/coverage/coverage-{unique_node_id}.info'
                # Upload OTBR code coverage if OTBR_COVERAGE=1, otherwise OpenThread code coverage.
                if OTBR_COVERAGE:
                    codecov_cmd = f'lcov --directory . --capture --output-file {cov_file_path}'
                else:
                    codecov_cmd = ('lcov --directory build/otbr/third_party/openthread/repo --capture '
                                   f'--output-file {cov_file_path}')

                self.bash(codecov_cmd)

            copyCore = subprocess.run(f'docker cp {self._docker_name}:/core ./coredump_{unique_node_id}', shell=True)
            if copyCore.returncode == 0:
                subprocess.check_call(
                    f'docker cp {self._docker_name}:/usr/sbin/otbr-agent ./otbr-agent_{unique_node_id}', shell=True)

        finally:
            subprocess.check_call(f"docker rm -f {self._docker_name}", shell=True)
            self._docker_proc.wait()
            del self._docker_proc

    def _shutdown_ot_rcp(self):
        if self._ot_rcp_proc is not None:
            self._ot_rcp_proc.kill()
            self._ot_rcp_proc.wait()
            del self._ot_rcp_proc

    def _shutdown_socat(self):
        if self._socat_proc is not None:
            self._socat_proc.stderr.close()
            self._socat_proc.kill()
            self._socat_proc.wait()
            del self._socat_proc

    def bash(self, cmd: str, encoding='ascii') -> List[str]:
        logging.info("%s $ %s", self, cmd)
        proc = subprocess.Popen(['docker', 'exec', '-i', self._docker_name, 'bash', '-c', cmd],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=sys.stderr,
                                encoding=encoding)

        with proc:

            lines = []

            while True:
                line = proc.stdout.readline()

                if not line:
                    break

                lines.append(line)
                logging.info("%s $ %r", self, line.rstrip('\r\n'))

            proc.wait()

            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd, ''.join(lines))
            else:
                return lines

    def dns_dig(self, server: str, name: str, qtype: str):
        """
        Run dig command to query a DNS server.

        Args:
            server: the server address.
            name: the name to query.
            qtype: the query type (e.g. AAAA, PTR, TXT, SRV).

        Returns:
            The dig result similar as below:
            {
                "opcode": "QUERY",
                "status": "NOERROR",
                "id": "64144",
                "QUESTION": [
                    ('google.com.', 'IN', 'AAAA')
                ],
                "ANSWER": [
                    ('google.com.', 107,	'IN', 'AAAA', '2404:6800:4008:c00::71'),
                    ('google.com.', 107,	'IN', 'AAAA', '2404:6800:4008:c00::8a'),
                    ('google.com.', 107,	'IN', 'AAAA', '2404:6800:4008:c00::66'),
                    ('google.com.', 107,	'IN', 'AAAA', '2404:6800:4008:c00::8b'),
                ],
                "ADDITIONAL": [
                ],
            }
        """
        output = self.bash(f'dig -6 @{server} \'{name}\' {qtype}', encoding='raw_unicode_escape')

        section = None
        dig_result = {
            'QUESTION': [],
            'ANSWER': [],
            'ADDITIONAL': [],
        }

        for line in output:
            line = line.strip()

            if line.startswith(';; ->>HEADER<<- '):
                headers = line[len(';; ->>HEADER<<- '):].split(', ')
                for header in headers:
                    key, val = header.split(': ')
                    dig_result[key] = val

                continue

            if line == ';; QUESTION SECTION:':
                section = 'QUESTION'
                continue
            elif line == ';; ANSWER SECTION:':
                section = 'ANSWER'
                continue
            elif line == ';; ADDITIONAL SECTION:':
                section = 'ADDITIONAL'
                continue
            elif section and not line:
                section = None
                continue

            if section:
                assert line

                if section == 'QUESTION':
                    assert line.startswith(';')
                    line = line[1:]
                record = list(line.split())

                if section == 'QUESTION':
                    if record[2] in ('SRV', 'TXT'):
                        record[0] = self.__unescape_dns_instance_name(record[0])
                else:
                    record[1] = int(record[1])
                    if record[3] == 'SRV':
                        record[0] = self.__unescape_dns_instance_name(record[0])
                        record[4], record[5], record[6] = map(int, [record[4], record[5], record[6]])
                    elif record[3] == 'TXT':
                        record[0] = self.__unescape_dns_instance_name(record[0])
                        record[4:] = [self.__parse_dns_dig_txt(line)]
                    elif record[3] == 'PTR':
                        record[4] = self.__unescape_dns_instance_name(record[4])

                dig_result[section].append(tuple(record))

        return dig_result

    def call_dbus_method(self, *args):
        args = shlex.join([args[0], args[1], json.dumps(args[2:])])
        return json.loads(
            self.bash(f'python3 /app/third_party/openthread/repo/tests/scripts/thread-cert/call_dbus_method.py {args}')
            [0])

    def get_dbus_property(self, property_name):
        return self.call_dbus_method('org.freedesktop.DBus.Properties', 'Get', 'io.openthread.BorderRouter',
                                     property_name)

    def set_dbus_property(self, property_name, property_value):
        return self.call_dbus_method('org.freedesktop.DBus.Properties', 'Set', 'io.openthread.BorderRouter',
                                     property_name, property_value)

    def get_border_routing_counters(self):
        counters = self.get_dbus_property('BorderRoutingCounters')
        counters = {
            'inbound_unicast': counters[0],
            'inbound_multicast': counters[1],
            'outbound_unicast': counters[2],
            'outbound_multicast': counters[3],
            'ra_rx': counters[4],
            'ra_tx_success': counters[5],
            'ra_tx_failure': counters[6],
            'rs_rx': counters[7],
            'rs_tx_success': counters[8],
            'rs_tx_failure': counters[9],
        }
        logging.info(f'border routing counters: {counters}')
        return counters

    def _process_traffic_counters(self, counter):
        return {
            '4to6': {
                'packets': counter[0],
                'bytes': counter[1],
            },
            '6to4': {
                'packets': counter[2],
                'bytes': counter[3],
            }
        }

    def _process_packet_counters(self, counter):
        return {'4to6': {'packets': counter[0]}, '6to4': {'packets': counter[1]}}

    def nat64_set_enabled(self, enable):
        return self.call_dbus_method('io.openthread.BorderRouter', 'SetNat64Enabled', enable)

    def activate_ephemeral_key_mode(self, lifetime):
        return self.call_dbus_method('io.openthread.BorderRouter', 'ActivateEphemeralKeyMode', lifetime)

    def deactivate_ephemeral_key_mode(self, retain_active_session):
        return self.call_dbus_method('io.openthread.BorderRouter', 'DeactivateEphemeralKeyMode', retain_active_session)

    @property
    def nat64_cidr(self):
        self.send_command('nat64 cidr')
        cidr = self._expect_command_output()[0].strip()
        return ipaddress.IPv4Network(cidr, strict=False)

    @nat64_cidr.setter
    def nat64_cidr(self, cidr: ipaddress.IPv4Network):
        if not isinstance(cidr, ipaddress.IPv4Network):
            raise ValueError("cidr is expected to be an instance of ipaddress.IPv4Network")
        self.send_command(f'nat64 cidr {cidr}')
        self._expect_done()

    @property
    def nat64_state(self):
        state = self.get_dbus_property('Nat64State')
        return {'PrefixManager': state[0], 'Translator': state[1]}

    @property
    def nat64_mappings(self):
        return [{
            'id': row[0],
            'ip4': row[1],
            'ip6': row[2],
            'expiry': row[3],
            'counters': {
                'total': self._process_traffic_counters(row[4][0]),
                'ICMP': self._process_traffic_counters(row[4][1]),
                'UDP': self._process_traffic_counters(row[4][2]),
                'TCP': self._process_traffic_counters(row[4][3]),
            }
        } for row in self.get_dbus_property('Nat64Mappings')]

    @property
    def nat64_counters(self):
        res_error = self.get_dbus_property('Nat64ErrorCounters')
        res_proto = self.get_dbus_property('Nat64ProtocolCounters')
        return {
            'protocol': {
                'Total': self._process_traffic_counters(res_proto[0]),
                'ICMP': self._process_traffic_counters(res_proto[1]),
                'UDP': self._process_traffic_counters(res_proto[2]),
                'TCP': self._process_traffic_counters(res_proto[3]),
            },
            'errors': {
                'Unknown': self._process_packet_counters(res_error[0]),
                'Illegal Pkt': self._process_packet_counters(res_error[1]),
                'Unsup Proto': self._process_packet_counters(res_error[2]),
                'No Mapping': self._process_packet_counters(res_error[3]),
            }
        }

    @property
    def nat64_traffic_counters(self):
        res = self.get_dbus_property('Nat64TrafficCounters')
        return {
            'Total': self._process_traffic_counters(res[0]),
            'ICMP': self._process_traffic_counters(res[1]),
            'UDP': self._process_traffic_counters(res[2]),
            'TCP': self._process_traffic_counters(res[3]),
        }

    @property
    def dns_upstream_query_state(self):
        return bool(self.get_dbus_property('DnsUpstreamQueryState'))

    @dns_upstream_query_state.setter
    def dns_upstream_query_state(self, value):
        if type(value) is not bool:
            raise ValueError("dns_upstream_query_state must be a bool")
        return self.set_dbus_property('DnsUpstreamQueryState', value)

    @property
    def ephemeral_key_enabled(self):
        return bool(self.get_dbus_property('EphemeralKeyEnabled'))

    @ephemeral_key_enabled.setter
    def ephemeral_key_enabled(self, value):
        if type(value) is not bool:
            raise ValueError("ephemeral_key_enabled must be a bool")
        return self.set_dbus_property('EphemeralKeyEnabled', value)

    def read_border_routing_counters_delta(self):
        old_counters = self._border_routing_counters
        new_counters = self.get_border_routing_counters()
        self._border_routing_counters = new_counters
        delta_counters = {}
        if old_counters is None:
            delta_counters = new_counters
        else:
            for i in ('inbound', 'outbound'):
                for j in ('unicast', 'multicast'):
                    key = f'{i}_{j}'
                    assert (key in old_counters)
                    assert (key in new_counters)
                    value = [new_counters[key][0] - old_counters[key][0], new_counters[key][1] - old_counters[key][1]]
                    delta_counters[key] = value
        delta_counters = {
            key: value for key, value in delta_counters.items() if not isinstance(value, int) and value[0] and value[1]
        }

        return delta_counters

    @staticmethod
    def __unescape_dns_instance_name(name: str) -> str:
        new_name = []
        i = 0
        while i < len(name):
            c = name[i]

            if c == '\\':
                assert i + 1 < len(name), name
                if name[i + 1].isdigit():
                    assert i + 3 < len(name) and name[i + 2].isdigit() and name[i + 3].isdigit(), name
                    new_name.append(chr(int(name[i + 1:i + 4])))
                    i += 3
                else:
                    new_name.append(name[i + 1])
                    i += 1
            else:
                new_name.append(c)

            i += 1

        return ''.join(new_name)

    def __parse_dns_dig_txt(self, line: str):
        # Example TXT entry:
        # "xp=\\000\\013\\184\\000\\000\\000\\000\\000"
        txt = {}
        for entry in re.findall(r'"((?:[^\\]|\\.)*?)"', line):
            if entry == "":
                continue

            k, v = entry.split('=', 1)
            txt[k] = v

        return txt

    def _setup_sysctl(self):
        self.bash(f'sysctl net.ipv6.conf.{self.ETH_DEV}.accept_ra=2')
        self.bash(f'sysctl net.ipv6.conf.{self.ETH_DEV}.accept_ra_rt_info_max_plen=64')


class OtCli:
    RESET_DELAY = 0.1

    def __init__(self, nodeid, is_mtd=False, version=None, is_bbr=False, **kwargs):
        self.verbose = int(float(os.getenv('VERBOSE', 0)))
        self.node_type = os.getenv('NODE_TYPE', 'sim')
        self.env_version = os.getenv('THREAD_VERSION', '1.1')
        self.is_bbr = is_bbr
        self._initialized = False
        if os.getenv('COVERAGE', 0) and os.getenv('CC', 'gcc') == 'gcc':
            self._cmd_prefix = '/usr/bin/env GCOV_PREFIX=%s/ot-run/%s/ot-gcda.%d ' % (os.getenv(
                'top_srcdir', '.'), sys.argv[0], nodeid)
        else:
            self._cmd_prefix = ''

        if version is not None:
            self.version = version
        else:
            self.version = self.env_version

        mode = os.environ.get('USE_MTD') == '1' and is_mtd and 'mtd' or 'ftd'

        if self.node_type == 'soc':
            self.__init_soc(nodeid)
        elif self.node_type == 'ncp-sim':
            # TODO use mode after ncp-mtd is available.
            self.__init_ncp_sim(nodeid, 'ftd')
        else:
            self.__init_sim(nodeid, mode)

        if self.verbose:
            self.pexpect.logfile_read = sys.stdout.buffer

        self._initialized = True

    def __init_sim(self, nodeid, mode):
        """ Initialize a simulation node. """

        # Default command if no match below, will be overridden if below conditions are met.
        cmd = './ot-cli-%s' % (mode)

        # For Thread 1.2 MTD node, use ot-cli-mtd build regardless of OT_CLI_PATH
        if self.version != '1.1' and mode == 'mtd' and 'top_builddir' in os.environ:
            srcdir = os.environ['top_builddir']
            cmd = '%s/examples/apps/cli/ot-cli-%s %d' % (srcdir, mode, nodeid)

        # If Thread version of node matches the testing environment version.
        elif self.version == self.env_version:
            # Load Thread 1.2 BBR device when testing Thread 1.2 scenarios
            # which requires device with Backbone functionality.
            if self.version != '1.1' and self.is_bbr:
                if 'OT_CLI_PATH_BBR' in os.environ:
                    cmd = os.environ['OT_CLI_PATH_BBR']
                elif 'top_builddir_1_4_bbr' in os.environ:
                    srcdir = os.environ['top_builddir_1_4_bbr']
                    cmd = '%s/examples/apps/cli/ot-cli-%s' % (srcdir, mode)

            # Load Thread device of the testing environment version (may be 1.1 or 1.2)
            else:
                if 'OT_CLI_PATH' in os.environ:
                    cmd = os.environ['OT_CLI_PATH']
                elif 'top_builddir' in os.environ:
                    srcdir = os.environ['top_builddir']
                    cmd = '%s/examples/apps/cli/ot-cli-%s' % (srcdir, mode)

            if 'RADIO_DEVICE' in os.environ:
                cmd += ' --real-time-signal=+1 -v spinel+hdlc+uart://%s?forkpty-arg=%d' % (os.environ['RADIO_DEVICE'],
                                                                                           nodeid)
                self.is_posix = True
            else:
                cmd += ' %d' % nodeid

        # Load Thread 1.1 node when testing Thread 1.2 scenarios for interoperability
        elif self.version == '1.1':
            # Posix app
            if 'OT_CLI_PATH_1_1' in os.environ:
                cmd = os.environ['OT_CLI_PATH_1_1']
            elif 'top_builddir_1_1' in os.environ:
                srcdir = os.environ['top_builddir_1_1']
                cmd = '%s/examples/apps/cli/ot-cli-%s' % (srcdir, mode)

            if 'RADIO_DEVICE_1_1' in os.environ:
                cmd += ' --real-time-signal=+1 -v spinel+hdlc+uart://%s?forkpty-arg=%d' % (
                    os.environ['RADIO_DEVICE_1_1'], nodeid)
                self.is_posix = True
            else:
                cmd += ' %d' % nodeid

        print("%s" % cmd)

        self.pexpect = pexpect.popen_spawn.PopenSpawn(self._cmd_prefix + cmd, timeout=10)

        # Add delay to ensure that the process is ready to receive commands.
        timeout = 0.4
        while timeout > 0:
            self.pexpect.send('\r\n')
            try:
                self.pexpect.expect('> ', timeout=0.1)
                break
            except pexpect.TIMEOUT:
                timeout -= 0.1

    def __init_ncp_sim(self, nodeid, mode):
        """ Initialize an NCP simulation node. """

        # Default command if no match below, will be overridden if below conditions are met.
        cmd = 'spinel-cli.py -p ./ot-ncp-%s -n' % mode

        # If Thread version of node matches the testing environment version.
        if self.version == self.env_version:
            if 'RADIO_DEVICE' in os.environ:
                args = ' --real-time-signal=+1 spinel+hdlc+uart://%s?forkpty-arg=%d' % (os.environ['RADIO_DEVICE'],
                                                                                        nodeid)
                self.is_posix = True
            else:
                args = ''

            # Load Thread 1.2 BBR device when testing Thread 1.2 scenarios
            # which requires device with Backbone functionality.
            if self.version != '1.1' and self.is_bbr:
                if 'OT_NCP_PATH_1_4_BBR' in os.environ:
                    cmd = 'spinel-cli.py -p "%s%s" -n' % (
                        os.environ['OT_NCP_PATH_1_4_BBR'],
                        args,
                    )
                elif 'top_builddir_1_4_bbr' in os.environ:
                    srcdir = os.environ['top_builddir_1_4_bbr']
                    cmd = '%s/examples/apps/ncp/ot-ncp-%s' % (srcdir, mode)
                    cmd = 'spinel-cli.py -p "%s%s" -n' % (
                        cmd,
                        args,
                    )

            # Load Thread device of the testing environment version (may be 1.1 or 1.2).
            else:
                if 'OT_NCP_PATH' in os.environ:
                    cmd = 'spinel-cli.py -p "%s%s" -n' % (
                        os.environ['OT_NCP_PATH'],
                        args,
                    )
                elif 'top_builddir' in os.environ:
                    srcdir = os.environ['top_builddir']
                    cmd = '%s/examples/apps/ncp/ot-ncp-%s' % (srcdir, mode)
                    cmd = 'spinel-cli.py -p "%s%s" -n' % (
                        cmd,
                        args,
                    )

        # Load Thread 1.1 node when testing Thread 1.2 scenarios for interoperability.
        elif self.version == '1.1':
            if 'RADIO_DEVICE_1_1' in os.environ:
                args = ' --real-time-signal=+1 spinel+hdlc+uart://%s?forkpty-arg=%d' % (os.environ['RADIO_DEVICE_1_1'],
                                                                                        nodeid)
                self.is_posix = True
            else:
                args = ''

            if 'OT_NCP_PATH_1_1' in os.environ:
                cmd = 'spinel-cli.py -p "%s%s" -n' % (
                    os.environ['OT_NCP_PATH_1_1'],
                    args,
                )
            elif 'top_builddir_1_1' in os.environ:
                srcdir = os.environ['top_builddir_1_1']
                cmd = '%s/examples/apps/ncp/ot-ncp-%s' % (srcdir, mode)
                cmd = 'spinel-cli.py -p "%s%s" -n' % (
                    cmd,
                    args,
                )

        cmd += ' %d' % nodeid
        print("%s" % cmd)

        self.pexpect = pexpect.spawn(self._cmd_prefix + cmd, timeout=10)

        # Add delay to ensure that the process is ready to receive commands.
        time.sleep(0.2)
        self._expect('spinel-cli >')
        self.debug(int(os.getenv('DEBUG', '0')))

    def __init_soc(self, nodeid):
        """ Initialize a System-on-a-chip node connected via UART. """
        import fdpexpect

        serialPort = '/dev/ttyUSB%d' % ((nodeid - 1) * 2)
        self.pexpect = fdpexpect.fdspawn(os.open(serialPort, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY))

    def destroy(self):
        if not self._initialized:
            return

        if (hasattr(self.pexpect, 'proc') and self.pexpect.proc.poll() is None or
                not hasattr(self.pexpect, 'proc') and self.pexpect.isalive()):
            print("%d: exit" % self.nodeid)
            self.pexpect.send('exit\n')
            self.pexpect.expect(pexpect.EOF)
            self.pexpect.wait()
            self._initialized = False


class NodeImpl:
    is_host = False
    is_otbr = False

    def __init__(self, nodeid, name=None, simulator=None, **kwargs):
        self.nodeid = nodeid
        self.name = name or ('Node%d' % nodeid)
        self.is_posix = False

        self.simulator = simulator
        if self.simulator:
            self.simulator.add_node(self)

        super().__init__(nodeid, **kwargs)

        self.set_addr64('%016x' % (thread_cert.EXTENDED_ADDRESS_BASE + nodeid))

    def _expect(self, pattern, timeout=-1, *args, **kwargs):
        """ Process simulator events until expected the pattern. """
        if timeout == -1:
            timeout = self.pexpect.timeout

        assert timeout > 0

        while timeout > 0:
            try:
                return self.pexpect.expect(pattern, 0.1, *args, **kwargs)
            except pexpect.TIMEOUT:
                timeout -= 0.1
                self.simulator.go(0)
                if timeout <= 0:
                    raise

    def _expect_done(self, timeout=-1):
        self._expect('Done', timeout)

    def _expect_result(self, pattern, *args, **kwargs):
        """Expect a single matching result.

        The arguments are identical to pexpect.expect().

        Returns:
            The matched line.
        """
        results = self._expect_results(pattern, *args, **kwargs)
        assert len(results) == 1, results
        return results[0]

    def _expect_results(self, pattern, *args, **kwargs):
        """Expect multiple matching results.

        The arguments are identical to pexpect.expect().

        Returns:
            The matched lines.
        """
        output = self._expect_command_output()
        results = [line for line in output if self._match_pattern(line, pattern)]
        return results

    def _expect_key_value_pairs(self, pattern, separator=': '):
        """Expect 'key: value' in multiple lines.

        Returns:
            Dictionary of the key:value pairs.
        """
        result = {}
        for line in self._expect_results(pattern):
            key, val = line.split(separator)
            result.update({key: val})
        return result

    @staticmethod
    def _match_pattern(line, pattern):
        if isinstance(pattern, str):
            pattern = re.compile(pattern)

        if isinstance(pattern, typing.Pattern):
            return pattern.match(line)
        else:
            return any(NodeImpl._match_pattern(line, p) for p in pattern)

    def _expect_command_output(self, ignore_logs=True):
        lines = []

        while True:
            line = self.__readline(ignore_logs=ignore_logs)

            if line == 'Done':
                break
            elif line.startswith('Error '):
                raise Exception(line)
            else:
                lines.append(line)

        print(f'_expect_command_output() returns {lines!r}')
        return lines

    def __is_logging_line(self, line: str) -> bool:
        return len(line) >= 3 and line[:3] in {'[D]', '[I]', '[N]', '[W]', '[C]', '[-]'}

    def read_cert_messages_in_commissioning_log(self, timeout=-1):
        """Get the log of the traffic after DTLS handshake.
        """
        format_str = br"=+?\[\[THCI\].*?type=%s.*?\].*?=+?[\s\S]+?-{40,}"
        join_fin_req = format_str % br"JOIN_FIN\.req"
        join_fin_rsp = format_str % br"JOIN_FIN\.rsp"
        dummy_format_str = br"\[THCI\].*?type=%s.*?"
        join_ent_ntf = dummy_format_str % br"JOIN_ENT\.ntf"
        join_ent_rsp = dummy_format_str % br"JOIN_ENT\.rsp"
        pattern = (b"(" + join_fin_req + b")|(" + join_fin_rsp + b")|(" + join_ent_ntf + b")|(" + join_ent_rsp + b")")

        messages = []
        # There are at most 4 cert messages both for joiner and commissioner
        for _ in range(0, 4):
            try:
                self._expect(pattern, timeout=timeout)
                log = self.pexpect.match.group(0)
                messages.append(self._extract_cert_message(log))
            except BaseException:
                break
        return messages

    def _extract_cert_message(self, log):
        res = re.search(br"direction=\w+", log)
        assert res
        direction = res.group(0).split(b'=')[1].strip()

        res = re.search(br"type=\S+", log)
        assert res
        type = res.group(0).split(b'=')[1].strip()

        payload = bytearray([])
        payload_len = 0
        if type in [b"JOIN_FIN.req", b"JOIN_FIN.rsp"]:
            res = re.search(br"len=\d+", log)
            assert res
            payload_len = int(res.group(0).split(b'=')[1].strip())

            hex_pattern = br"\|(\s([0-9a-fA-F]{2}|\.\.))+?\s+?\|"
            while True:
                res = re.search(hex_pattern, log)
                if not res:
                    break
                data = [int(hex, 16) for hex in res.group(0)[1:-1].split(b' ') if hex and hex != b'..']
                payload += bytearray(data)
                log = log[res.end() - 1:]
        assert len(payload) == payload_len
        return (direction, type, payload)

    def send_command(self, cmd, go=True, expect_command_echo=True, maybeoff=False):
        print("%d: %s" % (self.nodeid, cmd))
        self.pexpect.send(cmd + '\n')
        if go:
            self.simulator.go(0, nodeid=self.nodeid, maybeoff=maybeoff)
        sys.stdout.flush()

        if expect_command_echo:
            self._expect_command_echo(cmd)

    def _expect_command_echo(self, cmd):
        cmd = cmd.strip()
        while True:
            line = self.__readline()
            if line.strip() == cmd:
                break

            logging.warning("expecting echo %r, but read %r", cmd, line)

    def __readline(self, ignore_logs=True):
        PROMPT = 'spinel-cli > ' if self.node_type == 'ncp-sim' else '> '
        while True:
            self._expect(r"[^\n]+\n")
            line = self.pexpect.match.group(0).decode('utf8').strip()
            while line.startswith(PROMPT):
                line = line[len(PROMPT):]

            if line == '':
                continue

            if ignore_logs and self.__is_logging_line(line):
                continue

            return line

    def get_commands(self):
        self.send_command('?')
        self._expect('Commands:')
        return self._expect_results(r'\S+')

    def set_mode(self, mode):
        cmd = 'mode %s' % mode
        self.send_command(cmd)
        self._expect_done()

    def debug(self, level):
        # `debug` command will not trigger interaction with simulator
        self.send_command('debug %d' % level, go=False)

    def start(self):
        self.interface_up()
        self.thread_start()

    def stop(self):
        self.thread_stop()
        self.interface_down()

    def set_log_level(self, level: int):
        self.send_command(f'log level {level}')
        self._expect_done()

    def interface_up(self):
        self.send_command('ifconfig up')
        self._expect_done()

    def interface_down(self):
        self.send_command('ifconfig down')
        self._expect_done()

    def thread_start(self):
        self.send_command('thread start')
        self._expect_done()

    def thread_stop(self):
        self.send_command('thread stop')
        self._expect_done()

    def detach(self, is_async=False):
        cmd = 'detach'
        if is_async:
            cmd += ' async'

        self.send_command(cmd)

        if is_async:
            self._expect_done()
            return

        end = self.simulator.now() + 4
        while True:
            self.simulator.go(1)
            try:
                self._expect_done(timeout=0.1)
                return
            except (pexpect.TIMEOUT, socket.timeout):
                if self.simulator.now() > end:
                    raise

    def expect_finished_detaching(self):
        self._expect('Finished detaching')

    def commissioner_start(self):
        cmd = 'commissioner start'
        self.send_command(cmd)
        self._expect_done()

    def commissioner_stop(self):
        cmd = 'commissioner stop'
        self.send_command(cmd)
        self._expect_done()

    def commissioner_state(self):
        states = [r'disabled', r'petitioning', r'active']
        self.send_command('commissioner state')
        return self._expect_result(states)

    def commissioner_add_joiner(self, addr, psk):
        cmd = 'commissioner joiner add %s %s' % (addr, psk)
        self.send_command(cmd)
        self._expect_done()

    def commissioner_set_provisioning_url(self, provisioning_url=''):
        cmd = 'commissioner provisioningurl %s' % provisioning_url
        self.send_command(cmd)
        self._expect_done()

    def joiner_start(self, pskd='', provisioning_url=''):
        cmd = 'joiner start %s %s' % (pskd, provisioning_url)
        self.send_command(cmd)
        self._expect_done()

    def clear_allowlist(self):
        cmd = 'macfilter addr clear'
        self.send_command(cmd)
        self._expect_done()

    def enable_allowlist(self):
        cmd = 'macfilter addr allowlist'
        self.send_command(cmd)
        self._expect_done()

    def disable_allowlist(self):
        cmd = 'macfilter addr disable'
        self.send_command(cmd)
        self._expect_done()

    def add_allowlist(self, addr, rssi=None):
        cmd = 'macfilter addr add %s' % addr

        if rssi is not None:
            cmd += ' %s' % rssi

        self.send_command(cmd)
        self._expect_done()

    def radiofilter_is_enabled(self) -> bool:
        states = [r'Disabled', r'Enabled']
        self.send_command('radiofilter')
        return self._expect_result(states) == 'Enabled'

    def radiofilter_enable(self):
        cmd = 'radiofilter enable'
        self.send_command(cmd)
        self._expect_done()

    def radiofilter_disable(self):
        cmd = 'radiofilter disable'
        self.send_command(cmd)
        self._expect_done()

    def get_bbr_registration_jitter(self):
        self.send_command('bbr jitter')
        return int(self._expect_result(r'\d+'))

    def set_bbr_registration_jitter(self, jitter):
        cmd = 'bbr jitter %d' % jitter
        self.send_command(cmd)
        self._expect_done()

    def get_rcp_version(self) -> str:
        self.send_command('rcp version')
        rcp_version = self._expect_command_output()[0].strip()
        return rcp_version

    def srp_server_get_state(self):
        states = ['disabled', 'running', 'stopped']
        self.send_command('srp server state')
        return self._expect_result(states)

    def srp_server_get_addr_mode(self):
        modes = [r'unicast', r'anycast']
        self.send_command(f'srp server addrmode')
        return self._expect_result(modes)

    def srp_server_set_addr_mode(self, mode):
        self.send_command(f'srp server addrmode {mode}')
        self._expect_done()

    def srp_server_get_anycast_seq_num(self):
        self.send_command(f'srp server seqnum')
        return int(self._expect_result(r'\d+'))

    def srp_server_set_anycast_seq_num(self, seqnum):
        self.send_command(f'srp server seqnum {seqnum}')
        self._expect_done()

    def srp_server_set_enabled(self, enable):
        cmd = f'srp server {"enable" if enable else "disable"}'
        self.send_command(cmd)
        self._expect_done()

    def srp_server_set_lease_range(self, min_lease, max_lease, min_key_lease, max_key_lease):
        self.send_command(f'srp server lease {min_lease} {max_lease} {min_key_lease} {max_key_lease}')
        self._expect_done()

    def srp_server_set_ttl_range(self, min_ttl, max_ttl):
        self.send_command(f'srp server ttl {min_ttl} {max_ttl}')
        self._expect_done()

    def srp_server_get_hosts(self):
        """Returns the host list on the SRP server as a list of property
           dictionary.

           Example output:
           [{
               'fullname': 'my-host.default.service.arpa.',
               'name': 'my-host',
               'deleted': 'false',
               'addresses': ['2001::1', '2001::2']
           }]
        """

        cmd = 'srp server host'
        self.send_command(cmd)
        lines = self._expect_command_output()
        host_list = []
        while lines:
            host = {}

            host['fullname'] = lines.pop(0).strip()
            host['name'] = host['fullname'].split('.')[0]

            host['deleted'] = lines.pop(0).strip().split(':')[1].strip()
            if host['deleted'] == 'true':
                host_list.append(host)
                continue

            addresses = lines.pop(0).strip().split('[')[1].strip(' ]').split(',')
            map(str.strip, addresses)
            host['addresses'] = [addr.strip() for addr in addresses if addr]

            host_list.append(host)

        return host_list

    def srp_server_get_host(self, host_name):
        """Returns host on the SRP server that matches given host name.

           Example usage:
           self.srp_server_get_host("my-host")
        """

        for host in self.srp_server_get_hosts():
            if host_name == host['name']:
                return host

    def srp_server_get_services(self):
        """Returns the service list on the SRP server as a list of property
           dictionary.

           Example output:
           [{
               'fullname': 'my-service._ipps._tcp.default.service.arpa.',
               'instance': 'my-service',
               'name': '_ipps._tcp',
               'deleted': 'false',
               'port': '12345',
               'priority': '0',
               'weight': '0',
               'ttl': '7200',
               'lease': '7200',
               'key-lease': '7200',
               'TXT': ['abc=010203'],
               'host_fullname': 'my-host.default.service.arpa.',
               'host': 'my-host',
               'addresses': ['2001::1', '2001::2']
           }]

           Note that the TXT data is output as a HEX string.
        """

        cmd = 'srp server service'
        self.send_command(cmd)
        lines = self._expect_command_output()

        service_list = []
        while lines:
            service = {}

            service['fullname'] = lines.pop(0).strip()
            name_labels = service['fullname'].split('.')
            service['instance'] = name_labels[0]
            service['name'] = '.'.join(name_labels[1:3])

            service['deleted'] = lines.pop(0).strip().split(':')[1].strip()
            if service['deleted'] == 'true':
                service_list.append(service)
                continue

            # 'subtypes', port', 'priority', 'weight', 'ttl', 'lease', and 'key-lease'
            for i in range(0, 7):
                key_value = lines.pop(0).strip().split(':')
                service[key_value[0].strip()] = key_value[1].strip()

            txt_entries = lines.pop(0).strip().split('[')[1].strip(' ]').split(',')
            txt_entries = map(str.strip, txt_entries)
            service['TXT'] = [txt for txt in txt_entries if txt]

            service['host_fullname'] = lines.pop(0).strip().split(':')[1].strip()
            service['host'] = service['host_fullname'].split('.')[0]

            addresses = lines.pop(0).strip().split('[')[1].strip(' ]').split(',')
            addresses = map(str.strip, addresses)
            service['addresses'] = [addr for addr in addresses if addr]

            service_list.append(service)

        return service_list

    def srp_server_get_service(self, instance_name, service_name):
        """Returns service on the SRP server that matches given instance
           name and service name.

           Example usage:
           self.srp_server_get_service("my-service", "_ipps._tcp")
        """

        for service in self.srp_server_get_services():
            if (instance_name == service['instance'] and service_name == service['name']):
                return service

    def get_srp_server_port(self):
        self.send_command('srp server port')
        return int(self._expect_result(r'\d+'))

    def srp_client_start(self, server_address, server_port):
        self.send_command(f'srp client start {server_address} {server_port}')
        self._expect_done()

    def srp_client_stop(self):
        self.send_command(f'srp client stop')
        self._expect_done()

    def srp_client_get_state(self):
        cmd = 'srp client state'
        self.send_command(cmd)
        return self._expect_command_output()[0]

    def srp_client_get_auto_start_mode(self):
        cmd = 'srp client autostart'
        self.send_command(cmd)
        return self._expect_command_output()[0]

    def srp_client_enable_auto_start_mode(self):
        self.send_command(f'srp client autostart enable')
        self._expect_done()

    def srp_client_disable_auto_start_mode(self):
        self.send_command(f'srp client autostart disable')
        self._expect_done()

    def srp_client_get_server_address(self):
        cmd = 'srp client server address'
        self.send_command(cmd)
        return self._expect_command_output()[0]

    def srp_client_get_server_port(self):
        cmd = 'srp client server port'
        self.send_command(cmd)
        return int(self._expect_command_output()[0])

    def srp_client_get_host_state(self):
        cmd = 'srp client host state'
        self.send_command(cmd)
        return self._expect_command_output()[0]

    def srp_client_set_host_name(self, name):
        self.send_command(f'srp client host name {name}')
        self._expect_done()

    def srp_client_get_host_name(self):
        self.send_command(f'srp client host name')
        self._expect_done()

    def srp_client_remove_host(self, remove_key=False, send_unreg_to_server=False):
        self.send_command(f'srp client host remove {int(remove_key)} {int(send_unreg_to_server)}')
        self._expect_done()

    def srp_client_clear_host(self):
        self.send_command(f'srp client host clear')
        self._expect_done()

    def srp_client_enable_auto_host_address(self):
        self.send_command(f'srp client host address auto')
        self._expect_done()

    def srp_client_set_host_address(self, *addrs: str):
        self.send_command(f'srp client host address {" ".join(addrs)}')
        self._expect_done()

    def srp_client_get_host_address(self):
        self.send_command(f'srp client host address')
        self._expect_done()

    def srp_client_add_service(self,
                               instance_name,
                               service_name,
                               port,
                               priority=0,
                               weight=0,
                               txt_entries=[],
                               lease=0,
                               key_lease=0):
        txt_record = "".join(self._encode_txt_entry(entry) for entry in txt_entries)
        if txt_record == '':
            txt_record = '-'
        instance_name = self._escape_escapable(instance_name)
        self.send_command(
            f'srp client service add {instance_name} {service_name} {port} {priority} {weight} {txt_record} {lease} {key_lease}'
        )
        self._expect_done()

    def srp_client_remove_service(self, instance_name, service_name):
        self.send_command(f'srp client service remove {instance_name} {service_name}')
        self._expect_done()

    def srp_client_clear_service(self, instance_name, service_name):
        self.send_command(f'srp client service clear {instance_name} {service_name}')
        self._expect_done()

    def srp_client_get_services(self):
        cmd = 'srp client service'
        self.send_command(cmd)
        service_lines = self._expect_command_output()
        return [self._parse_srp_client_service(line) for line in service_lines]

    def srp_client_set_lease_interval(self, leaseinterval: int):
        cmd = f'srp client leaseinterval {leaseinterval}'
        self.send_command(cmd)
        self._expect_done()

    def srp_client_get_lease_interval(self) -> int:
        cmd = 'srp client leaseinterval'
        self.send_command(cmd)
        return int(self._expect_result(r'\d+'))

    def srp_client_set_key_lease_interval(self, leaseinterval: int):
        cmd = f'srp client keyleaseinterval {leaseinterval}'
        self.send_command(cmd)
        self._expect_done()

    def srp_client_get_key_lease_interval(self) -> int:
        cmd = 'srp client keyleaseinterval'
        self.send_command(cmd)
        return int(self._expect_result(r'\d+'))

    def srp_client_set_ttl(self, ttl: int):
        cmd = f'srp client ttl {ttl}'
        self.send_command(cmd)
        self._expect_done()

    def srp_client_get_ttl(self) -> int:
        cmd = 'srp client ttl'
        self.send_command(cmd)
        return int(self._expect_result(r'\d+'))

    #
    # TREL utilities
    #

    def enable_trel(self):
        cmd = 'trel enable'
        self.send_command(cmd)
        self._expect_done()

    def is_trel_enabled(self) -> Union[None, bool]:
        states = [r'Disabled', r'Enabled']
        self.send_command('trel')
        try:
            return self._expect_result(states) == 'Enabled'
        except Exception as ex:
            if 'InvalidCommand' in str(ex):
                return None

            raise

    def get_trel_counters(self):
        cmd = 'trel counters'
        self.send_command(cmd)
        result = self._expect_command_output()

        counters = {}
        for line in result:
            m = re.match(r'(\w+)\:[^\d]+(\d+)[^\d]+(\d+)(?:[^\d]+(\d+))?', line)
            if m:
                groups = m.groups()
                sub_counters = {
                    'packets': int(groups[1]),
                    'bytes': int(groups[2]),
                }
                if groups[3]:
                    sub_counters['failures'] = int(groups[3])
                counters[groups[0]] = sub_counters
        return counters

    def reset_trel_counters(self):
        cmd = 'trel counters reset'
        self.send_command(cmd)
        self._expect_done()

    def get_trel_port(self):
        cmd = 'trel port'
        self.send_command(cmd)
        return int(self._expect_command_output()[0])

    def enable_border_agent(self):
        self.send_command('ba enable')
        self._expect_done()

    def disable_border_agent(self):
        self.send_command('ba disable')
        self._expect_done()

    def get_border_agent_counters(self):
        cmd = 'ba counters'
        self.send_command(cmd)
        result = self._expect_command_output()

        counters = {}
        for line in result:
            m = re.match(r'(\w+)\: (\d+)', line)
            if m:
                counter_name = m.group(1)
                counter_value = m.group(2)

                counters[counter_name] = int(counter_value)
        return counters

    def _encode_txt_entry(self, entry):
        """Encodes the TXT entry to the DNS-SD TXT record format as a HEX string.

           Example usage:
           self._encode_txt_entries(['abc'])     -> '03616263'
           self._encode_txt_entries(['def='])    -> '046465663d'
           self._encode_txt_entries(['xyz=XYZ']) -> '0778797a3d58595a'
        """
        return '{:02x}'.format(len(entry)) + "".join("{:02x}".format(ord(c)) for c in entry)

    def _parse_srp_client_service(self, line: str):
        """Parse one line of srp service list into a dictionary which
           maps string keys to string values.

           Example output for input
           'instance:\"%s\", name:\"%s\", state:%s, port:%d, priority:%d, weight:%d"'
           {
               'instance': 'my-service',
               'name': '_ipps._udp',
               'state': 'ToAdd',
               'port': '12345',
               'priority': '0',
               'weight': '0'
           }

           Note that value of 'port', 'priority' and 'weight' are represented
           as strings but not integers.
        """
        key_values = [word.strip().split(':') for word in line.split(', ')]
        keys = [key_value[0] for key_value in key_values]
        values = [key_value[1].strip('"') for key_value in key_values]
        return dict(zip(keys, values))

    def locate(self, anycast_addr):
        cmd = 'locate ' + anycast_addr
        self.send_command(cmd)
        self.simulator.go(5)
        return self._parse_locate_result(self._expect_command_output()[0])

    def _parse_locate_result(self, line: str):
        """Parse anycast locate result as list of ml-eid and rloc16.

           Example output for input
           'fd00:db8:0:0:acf9:9d0:7f3c:b06e 0xa800'

           [ 'fd00:db8:0:0:acf9:9d0:7f3c:b06e', '0xa800' ]
        """
        return line.split(' ')

    def enable_backbone_router(self):
        cmd = 'bbr enable'
        self.send_command(cmd)
        self._expect_done()

    def disable_backbone_router(self):
        cmd = 'bbr disable'
        self.send_command(cmd)
        self._expect_done()

    def register_backbone_router(self):
        cmd = 'bbr register'
        self.send_command(cmd)
        self._expect_done()

    def get_backbone_router_state(self):
        states = [r'Disabled', r'Primary', r'Secondary']
        self.send_command('bbr state')
        return self._expect_result(states)

    @property
    def is_primary_backbone_router(self) -> bool:
        return self.get_backbone_router_state() == 'Primary'

    def get_backbone_router(self):
        cmd = 'bbr config'
        self.send_command(cmd)
        self._expect(r'(.*)Done')
        g = self.pexpect.match.groups()
        output = g[0].decode("utf-8")
        lines = output.strip().split('\n')
        lines = [l.strip() for l in lines]
        ret = {}
        for l in lines:
            z = re.search(r'seqno:\s+([0-9]+)', l)
            if z:
                ret['seqno'] = int(z.groups()[0])

            z = re.search(r'delay:\s+([0-9]+)', l)
            if z:
                ret['delay'] = int(z.groups()[0])

            z = re.search(r'timeout:\s+([0-9]+)', l)
            if z:
                ret['timeout'] = int(z.groups()[0])

        return ret

    def set_backbone_router(self, seqno=None, reg_delay=None, mlr_timeout=None):
        cmd = 'bbr config'

        if seqno is not None:
            cmd += ' seqno %d' % seqno

        if reg_delay is not None:
            cmd += ' delay %d' % reg_delay

        if mlr_timeout is not None:
            cmd += ' timeout %d' % mlr_timeout

        self.send_command(cmd)
        self._expect_done()

    def set_domain_prefix(self, prefix, flags='prosD'):
        self.add_prefix(prefix, flags)
        self.register_netdata()

    def remove_domain_prefix(self, prefix):
        self.remove_prefix(prefix)
        self.register_netdata()

    def set_next_dua_response(self, status: Union[str, int], iid=None):
        # Convert 5.00 to COAP CODE 160
        if isinstance(status, str):
            assert '.' in status
            status = status.split('.')
            status = (int(status[0]) << 5) + int(status[1])

        cmd = 'bbr mgmt dua {}'.format(status)
        if iid is not None:
            cmd += ' ' + str(iid)
        self.send_command(cmd)
        self._expect_done()

    def set_dua_iid(self, iid: str):
        assert len(iid) == 16
        int(iid, 16)

        cmd = 'dua iid {}'.format(iid)
        self.send_command(cmd)
        self._expect_done()

    def clear_dua_iid(self):
        cmd = 'dua iid clear'
        self.send_command(cmd)
        self._expect_done()

    def multicast_listener_list(self) -> Dict[IPv6Address, int]:
        cmd = 'bbr mgmt mlr listener'
        self.send_command(cmd)

        table = {}
        for line in self._expect_results(r"\S+ \d+"):
            line = line.split()
            assert len(line) == 2, line
            ip = IPv6Address(line[0])
            timeout = int(line[1])
            assert ip not in table

            table[ip] = timeout

        return table

    def multicast_listener_clear(self):
        cmd = f'bbr mgmt mlr listener clear'
        self.send_command(cmd)
        self._expect_done()

    def multicast_listener_add(self, ip: Union[IPv6Address, str], timeout: int = 0):
        if not isinstance(ip, IPv6Address):
            ip = IPv6Address(ip)

        cmd = f'bbr mgmt mlr listener add {ip.compressed} {timeout}'
        self.send_command(cmd)
        self._expect(r"(Done|Error .*)")

    def set_next_mlr_response(self, status: int):
        cmd = 'bbr mgmt mlr response {}'.format(status)
        self.send_command(cmd)
        self._expect_done()

    def register_multicast_listener(self, *ipaddrs: Union[IPv6Address, str], timeout=None):
        assert len(ipaddrs) > 0, ipaddrs

        ipaddrs = map(str, ipaddrs)
        cmd = f'mlr reg {" ".join(ipaddrs)}'
        if timeout is not None:
            cmd += f' {int(timeout)}'
        self.send_command(cmd)
        self.simulator.go(3)
        lines = self._expect_command_output()
        m = re.match(r'status (\d+), (\d+) failed', lines[0])
        assert m is not None, lines
        status = int(m.group(1))
        failed_num = int(m.group(2))
        assert failed_num == len(lines) - 1
        failed_ips = list(map(IPv6Address, lines[1:]))
        print(f"register_multicast_listener {ipaddrs} => status: {status}, failed ips: {failed_ips}")
        return status, failed_ips

    def set_link_quality(self, addr, lqi):
        cmd = 'macfilter rss add-lqi %s %s' % (addr, lqi)
        self.send_command(cmd)
        self._expect_done()

    def set_outbound_link_quality(self, lqi):
        cmd = 'macfilter rss add-lqi * %s' % (lqi)
        self.send_command(cmd)
        self._expect_done()

    def remove_allowlist(self, addr):
        cmd = 'macfilter addr remove %s' % addr
        self.send_command(cmd)
        self._expect_done()

    def get_addr16(self):
        self.send_command('rloc16')
        rloc16 = self._expect_result(r'[0-9a-fA-F]{4}')
        return int(rloc16, 16)

    def get_router_id(self):
        rloc16 = self.get_addr16()
        return rloc16 >> 10

    def get_addr64(self):
        self.send_command('extaddr')
        return self._expect_result('[0-9a-fA-F]{16}')

    def set_addr64(self, addr64: str):
        # Make sure `addr64` is a hex string of length 16
        assert len(addr64) == 16
        int(addr64, 16)
        self.send_command('extaddr %s' % addr64)
        self._expect_done()

    def get_eui64(self):
        self.send_command('eui64')
        return self._expect_result('[0-9a-fA-F]{16}')

    def set_extpanid(self, extpanid):
        self.send_command('extpanid %s' % extpanid)
        self._expect_done()

    def get_extpanid(self):
        self.send_command('extpanid')
        return self._expect_result('[0-9a-fA-F]{16}')

    def get_mesh_local_prefix(self):
        self.send_command('prefix meshlocal')
        return self._expect_command_output()[0]

    def set_mesh_local_prefix(self, mesh_local_prefix):
        self.send_command('prefix meshlocal %s' % mesh_local_prefix)
        self._expect_done()

    def get_joiner_id(self):
        self.send_command('joiner id')
        return self._expect_result('[0-9a-fA-F]{16}')

    def get_channel(self):
        self.send_command('channel')
        return int(self._expect_result(r'\d+'))

    def set_channel(self, channel):
        cmd = 'channel %d' % channel
        self.send_command(cmd)
        self._expect_done()

    def get_networkkey(self):
        self.send_command('networkkey')
        return self._expect_result('[0-9a-fA-F]{32}')

    def set_networkkey(self, networkkey):
        cmd = 'networkkey %s' % networkkey
        self.send_command(cmd)
        self._expect_done()
        self.simulator.add_network_key(network_key)

    def get_key_sequence_counter(self):
        self.send_command('keysequence counter')
        result = self._expect_result(r'\d+')
        return int(result)

    def set_key_sequence_counter(self, key_sequence_counter):
        cmd = 'keysequence counter %d' % key_sequence_counter
        self.send_command(cmd)
        self._expect_done()

    def get_key_switch_guardtime(self):
        self.send_command('keysequence guardtime')
        return int(self._expect_result(r'\d+'))

    def set_key_switch_guardtime(self, key_switch_guardtime):
        cmd = 'keysequence guardtime %d' % key_switch_guardtime
        self.send_command(cmd)
        self._expect_done()

    def set_network_id_timeout(self, network_id_timeout):
        cmd = 'networkidtimeout %d' % network_id_timeout
        self.send_command(cmd)
        self._expect_done()

    def _escape_escapable(self, string):
        """Escape CLI escapable characters in the given string.

        Args:
            string (str): UTF-8 input string.

        Returns:
            [str]: The modified string with escaped characters.
        """
        escapable_chars = '\\ \t\r\n'
        for char in escapable_chars:
            string = string.replace(char, '\\%s' % char)
        return string

    def get_network_name(self):
        self.send_command('networkname')
        return self._expect_result([r'\S+'])

    def set_network_name(self, network_name):
        cmd = 'networkname %s' % self._escape_escapable(network_name)
        self.send_command(cmd)
        self._expect_done()

    def get_panid(self):
        self.send_command('panid')
        result = self._expect_result('0x[0-9a-fA-F]{4}')
        return int(result, 16)

    def set_panid(self, panid=config.PANID):
        cmd = 'panid %d' % panid
        self.send_command(cmd)
        self._expect_done()

    def set_parent_priority(self, priority):
        cmd = 'parentpriority %d' % priority
        self.send_command(cmd)
        self._expect_done()

    def get_partition_id(self):
        self.send_command('partitionid')
        return self._expect_result(r'\d+')

    def get_preferred_partition_id(self):
        self.send_command('partitionid preferred')
        return self._expect_result(r'\d+')

    def set_preferred_partition_id(self, partition_id):
        cmd = 'partitionid preferred %d' % partition_id
        self.send_command(cmd)
        self._expect_done()

    def get_pollperiod(self):
        self.send_command('pollperiod')
        return self._expect_result(r'\d+')

    def set_pollperiod(self, pollperiod):
        self.send_command('pollperiod %d' % pollperiod)
        self._expect_done()

    def get_child_supervision_interval(self):
        self.send_command('childsupervision interval')
        return self._expect_result(r'\d+')

    def set_child_supervision_interval(self, interval):
        self.send_command('childsupervision interval %d' % interval)
        self._expect_done()

    def get_child_supervision_check_timeout(self):
        self.send_command('childsupervision checktimeout')
        return self._expect_result(r'\d+')

    def set_child_supervision_check_timeout(self, timeout):
        self.send_command('childsupervision checktimeout %d' % timeout)
        self._expect_done()

    def get_child_supervision_check_failure_counter(self):
        self.send_command('childsupervision failcounter')
        return self._expect_result(r'\d+')

    def reset_child_supervision_check_failure_counter(self):
        self.send_command('childsupervision failcounter reset')
        self._expect_done()

    def get_csl_info(self):
        self.send_command('csl')
        return self._expect_key_value_pairs(r'\S+')

    def set_csl_channel(self, csl_channel):
        self.send_command('csl channel %d' % csl_channel)
        self._expect_done()

    def set_csl_period(self, csl_period):
        self.send_command('csl period %d' % csl_period)
        self._expect_done()

    def set_csl_timeout(self, csl_timeout):
        self.send_command('csl timeout %d' % csl_timeout)
        self._expect_done()

    def send_mac_emptydata(self):
        self.send_command('mac send emptydata')
        self._expect_done()

    def send_mac_datarequest(self):
        self.send_command('mac send datarequest')
        self._expect_done()

    def set_router_upgrade_threshold(self, threshold):
        cmd = 'routerupgradethreshold %d' % threshold
        self.send_command(cmd)
        self._expect_done()

    def set_router_downgrade_threshold(self, threshold):
        cmd = 'routerdowngradethreshold %d' % threshold
        self.send_command(cmd)
        self._expect_done()

    def get_router_downgrade_threshold(self) -> int:
        self.send_command('routerdowngradethreshold')
        return int(self._expect_result(r'\d+'))

    def set_router_eligible(self, enable: bool):
        cmd = f'routereligible {"enable" if enable else "disable"}'
        self.send_command(cmd)
        self._expect_done()

    def get_router_eligible(self) -> bool:
        states = [r'Disabled', r'Enabled']
        self.send_command('routereligible')
        return self._expect_result(states) == 'Enabled'

    def prefer_router_id(self, router_id):
        cmd = 'preferrouterid %d' % router_id
        self.send_command(cmd)
        self._expect_done()

    def release_router_id(self, router_id):
        cmd = 'releaserouterid %d' % router_id
        self.send_command(cmd)
        self._expect_done()

    def get_state(self):
        states = [r'detached', r'child', r'router', r'leader', r'disabled']
        self.send_command('state')
        return self._expect_result(states)

    def set_state(self, state):
        cmd = 'state %s' % state
        self.send_command(cmd)
        self._expect_done()

    def get_ba_state(self):
        states = [r'Disabled', r'Inactive', r'Active']
        self.send_command('ba state')
        return self._expect_result(states)

    def get_ephemeral_key_state(self):
        cmd = 'ba ephemeralkey'
        states = [r'Disabled', r'Stopped', r'Started', r'Connected', r'Accepted']
        self.send_command(cmd)
        return self._expect_result(states)

    def get_timeout(self):
        self.send_command('childtimeout')
        return self._expect_result(r'\d+')

    def set_timeout(self, timeout):
        cmd = 'childtimeout %d' % timeout
        self.send_command(cmd)
        self._expect_done()

    def set_max_children(self, number):
        cmd = 'childmax %d' % number
        self.send_command(cmd)
        self._expect_done()

    def get_weight(self):
        self.send_command('leaderweight')
        return self._expect_result(r'\d+')

    def set_weight(self, weight):
        cmd = 'leaderweight %d' % weight
        self.send_command(cmd)
        self._expect_done()

    def add_ipaddr(self, ipaddr):
        cmd = 'ipaddr add %s' % ipaddr
        self.send_command(cmd)
        self._expect_done()

    def del_ipaddr(self, ipaddr):
        cmd = 'ipaddr del %s' % ipaddr
        self.send_command(cmd)
        self._expect_done()

    def add_ipmaddr(self, ipmaddr):
        cmd = 'ipmaddr add %s' % ipmaddr
        self.send_command(cmd)
        self._expect_done()

    def del_ipmaddr(self, ipmaddr):
        cmd = 'ipmaddr del %s' % ipmaddr
        self.send_command(cmd)
        self._expect_done()

    def get_addrs(self, verbose=False):
        self.send_command('ipaddr' + (' -v' if verbose else ''))

        return self._expect_results(r'\S+(:\S*)+')

    def get_mleid(self):
        self.send_command('ipaddr mleid')
        return self._expect_result(r'\S+(:\S*)+')

    def get_linklocal(self):
        self.send_command('ipaddr linklocal')
        return self._expect_result(r'\S+(:\S*)+')

    def get_rloc(self):
        self.send_command('ipaddr rloc')
        return self._expect_result(r'\S+(:\S*)+')

    def get_addr(self, prefix):
        network = ipaddress.ip_network(u'%s' % str(prefix))
        addrs = self.get_addrs()

        for addr in addrs:
            if isinstance(addr, bytearray):
                addr = bytes(addr)
            ipv6_address = ipaddress.ip_address(addr)
            if ipv6_address in network:
                return ipv6_address.exploded

        return None

    def has_ipaddr(self, address):
        ipaddr = ipaddress.ip_address(address)
        ipaddrs = self.get_addrs()
        for addr in ipaddrs:
            if isinstance(addr, bytearray):
                addr = bytes(addr)
            if ipaddress.ip_address(addr) == ipaddr:
                return True
        return False

    def get_ipmaddrs(self):
        self.send_command('ipmaddr')
        return self._expect_results(r'\S+(:\S*)+')

    def has_ipmaddr(self, address):
        ipmaddr = ipaddress.ip_address(address)
        ipmaddrs = self.get_ipmaddrs()
        for addr in ipmaddrs:
            if isinstance(addr, bytearray):
                addr = bytes(addr)
            if ipaddress.ip_address(addr) == ipmaddr:
                return True
        return False

    def get_addr_leader_aloc(self):
        addrs = self.get_addrs()
        for addr in addrs:
            segs = addr.split(':')
            if (segs[4] == '0' and segs[5] == 'ff' and segs[6] == 'fe00' and segs[7] == 'fc00'):
                return addr
        return None

    def get_mleid_iid(self):
        ml_eid = IPv6Address(self.get_mleid())
        return ml_eid.packed[8:].hex()

    def get_eidcaches(self):
        eidcaches = []
        self.send_command('eidcache')
        for line in self._expect_results(r'([a-fA-F0-9\:]+) ([a-fA-F0-9]+)'):
            eidcaches.append(line.split())

        return eidcaches

    def add_service(self, enterpriseNumber, serviceData, serverData):
        cmd = 'service add %s %s %s' % (
            enterpriseNumber,
            serviceData,
            serverData,
        )
        self.send_command(cmd)
        self._expect_done()

    def remove_service(self, enterpriseNumber, serviceData):
        cmd = 'service remove %s %s' % (enterpriseNumber, serviceData)
        self.send_command(cmd)
        self._expect_done()

    def get_child_table(self) -> Dict[int, Dict[str, Any]]:
        """Get the table of attached children."""
        cmd = 'child table'
        self.send_command(cmd)
        output = self._expect_command_output()

        #
        # Example output:
        # | ID  | RLOC16 | Timeout    | Age        | LQ In | C_VN |R|D|N|Ver|CSL|QMsgCnt|Suprvsn| Extended MAC     |
        # +-----+--------+------------+------------+-------+------+-+-+-+---+---+-------+-------+------------------+
        # |   1 | 0xc801 |        240 |         24 |     3 |  131 |1|0|0|  3| 0 |     0 |   129 | 4ecede68435358ac |
        # |   2 | 0xc802 |        240 |          2 |     3 |  131 |0|0|0|  3| 1 |     0 |     0 | a672a601d2ce37d8 |
        # Done
        #

        headers = self.__split_table_row(output[0])

        table = {}
        for line in output[2:]:
            line = line.strip()
            if not line:
                continue

            fields = self.__split_table_row(line)
            col = lambda colname: self.__get_table_col(colname, headers, fields)

            id = int(col("ID"))
            r, d, n = int(col("R")), int(col("D")), int(col("N"))
            mode = f'{"r" if r else ""}{"d" if d else ""}{"n" if n else ""}'

            table[int(id)] = {
                'id': int(id),
                'rloc16': int(col('RLOC16'), 16),
                'timeout': int(col('Timeout')),
                'age': int(col('Age')),
                'lq_in': int(col('LQ In')),
                'c_vn': int(col('C_VN')),
                'mode': mode,
                'extaddr': col('Extended MAC'),
                'ver': int(col('Ver')),
                'csl': bool(int(col('CSL'))),
                'qmsgcnt': int(col('QMsgCnt')),
                'suprvsn': int(col('Suprvsn'))
            }

        return table

    def __split_table_row(self, row: str) -> List[str]:
        if not (row.startswith('|') and row.endswith('|')):
            raise ValueError(row)

        fields = row.split('|')
        fields = [x.strip() for x in fields[1:-1]]
        return fields

    def __get_table_col(self, colname: str, headers: List[str], fields: List[str]) -> str:
        return fields[headers.index(colname)]

    def __getOmrAddress(self):
        prefixes = [prefix.split('::')[0] for prefix in self.get_prefixes()]
        omr_addrs = []
        for addr in self.get_addrs():
            for prefix in prefixes:
                if (addr.startswith(prefix)) and (addr != self.__getDua()):
                    omr_addrs.append(addr)
                    break

        return omr_addrs

    def __getLinkLocalAddress(self):
        for ip6Addr in self.get_addrs():
            if re.match(config.LINK_LOCAL_REGEX_PATTERN, ip6Addr, re.I):
                return ip6Addr

        return None

    def __getGlobalAddress(self):
        global_address = []
        for ip6Addr in self.get_addrs():
            if ((not re.match(config.LINK_LOCAL_REGEX_PATTERN, ip6Addr, re.I)) and
                (not re.match(config.MESH_LOCAL_PREFIX_REGEX_PATTERN, ip6Addr, re.I)) and
                (not re.match(config.ROUTING_LOCATOR_REGEX_PATTERN, ip6Addr, re.I))):
                global_address.append(ip6Addr)

        return global_address

    def __getRloc(self):
        for ip6Addr in self.get_addrs():
            if (re.match(config.MESH_LOCAL_PREFIX_REGEX_PATTERN, ip6Addr, re.I) and
                    re.match(config.ROUTING_LOCATOR_REGEX_PATTERN, ip6Addr, re.I) and
                    not (re.match(config.ALOC_FLAG_REGEX_PATTERN, ip6Addr, re.I))):
                return ip6Addr
        return None

    def __getAloc(self):
        aloc = []
        for ip6Addr in self.get_addrs():
            if (re.match(config.MESH_LOCAL_PREFIX_REGEX_PATTERN, ip6Addr, re.I) and
                    re.match(config.ROUTING_LOCATOR_REGEX_PATTERN, ip6Addr, re.I) and
                    re.match(config.ALOC_FLAG_REGEX_PATTERN, ip6Addr, re.I)):
                aloc.append(ip6Addr)

        return aloc

    def __getMleid(self):
        for ip6Addr in self.get_addrs():
            if re.match(config.MESH_LOCAL_PREFIX_REGEX_PATTERN, ip6Addr,
                        re.I) and not (re.match(config.ROUTING_LOCATOR_REGEX_PATTERN, ip6Addr, re.I)):
                return ip6Addr

        return None

    def __getDua(self) -> Optional[str]:
        for ip6Addr in self.get_addrs():
            if re.match(config.DOMAIN_PREFIX_REGEX_PATTERN, ip6Addr, re.I):
                return ip6Addr

        return None

    def get_ip6_address_by_prefix(self, prefix: Union[str, IPv6Network]) -> List[IPv6Address]:
        """Get addresses matched with given prefix.

        Args:
            prefix: the prefix to match against.
                    Can be either a string or ipaddress.IPv6Network.

        Returns:
            The IPv6 address list.
        """
        if isinstance(prefix, str):
            prefix = IPv6Network(prefix)
        addrs = map(IPv6Address, self.get_addrs())

        return [addr for addr in addrs if addr in prefix]

    def get_ip6_address(self, address_type):
        """Get specific type of IPv6 address configured on thread device.

        Args:
            address_type: the config.ADDRESS_TYPE type of IPv6 address.

        Returns:
            IPv6 address string.
        """
        if address_type == config.ADDRESS_TYPE.LINK_LOCAL:
            return self.__getLinkLocalAddress()
        elif address_type == config.ADDRESS_TYPE.GLOBAL:
            return self.__getGlobalAddress()
        elif address_type == config.ADDRESS_TYPE.RLOC:
            return self.__getRloc()
        elif address_type == config.ADDRESS_TYPE.ALOC:
            return self.__getAloc()
        elif address_type == config.ADDRESS_TYPE.ML_EID:
            return self.__getMleid()
        elif address_type == config.ADDRESS_TYPE.DUA:
            return self.__getDua()
        elif address_type == config.ADDRESS_TYPE.BACKBONE_GUA:
            return self._getBackboneGua()
        elif address_type == config.ADDRESS_TYPE.OMR:
            return self.__getOmrAddress()
        else:
            return None

    def get_context_reuse_delay(self):
        self.send_command('contextreusedelay')
        return self._expect_result(r'\d+')

    def set_context_reuse_delay(self, delay):
        cmd = 'contextreusedelay %d' % delay
        self.send_command(cmd)
        self._expect_done()

    def add_prefix(self, prefix, flags='paosr', prf='med'):
        cmd = 'prefix add %s %s %s' % (prefix, flags, prf)
        self.send_command(cmd)
        self._expect_done()

    def remove_prefix(self, prefix):
        cmd = 'prefix remove %s' % prefix
        self.send_command(cmd)
        self._expect_done()

    #
    # BR commands
    #
    def enable_br(self):
        self.send_command('br enable')
        self._expect_done()

    def disable_br(self):
        self.send_command('br disable')
        self._expect_done()

    def get_br_omr_prefix(self):
        cmd = 'br omrprefix local'
        self.send_command(cmd)
        return self._expect_command_output()[0]

    def get_br_peers(self) -> List[str]:
        # Example output of `br peers` command:
        #   rloc16:0xa800 age:00:00:50
        #   rloc16:0x6800 age:00:00:51
        #   Done
        self.send_command('br peers')
        return self._expect_command_output()

    def get_br_peers_rloc16s(self) -> List[int]:
        """parse `br peers` output and return the list of RLOC16s"""
        return [
            int(pair.split(':')[1], 16)
            for line in self.get_br_peers()
            for pair in line.split()
            if pair.split(':')[0] == 'rloc16'
        ]

    def get_br_routers(self) -> List[str]:
        # Example output of `br routers` command:
        #   fe80:0:0:0:42:acff:fe14:3 (M:0 O:0 S:1) ms-since-rx:144160 reachable:yes age:00:17:36 (peer BR)
        #   fe80:0:0:0:42:acff:fe14:2 (M:0 O:0 S:1) ms-since-rx:45179 reachable:yes age:00:17:36
        #   Done
        self.send_command('br routers')
        return self._expect_command_output()

    def get_br_routers_ip_addresses(self) -> List[IPv6Address]:
        """parse `br routers` output and return the list of IPv6 addresses"""
        return [IPv6Address(line.split()[0]) for line in self.get_br_routers()]

    def get_netdata_omr_prefixes(self):
        omr_prefixes = []
        for prefix in self.get_prefixes():
            prefix, flags = prefix.split()[:2]
            if 'a' in flags and 'o' in flags and 's' in flags and 'D' not in flags:
                omr_prefixes.append(prefix)

        return omr_prefixes

    def get_br_on_link_prefix(self):
        cmd = 'br onlinkprefix local'
        self.send_command(cmd)
        return self._expect_command_output()[0]

    def pd_get_prefix(self):
        cmd = 'br pd omrprefix'
        self.send_command(cmd)
        return self._expect_command_output()[0].split(" ")[0]

    def pd_set_enabled(self, enable):
        self.send_command('br pd {}'.format("enable" if enable else "disable"))
        self._expect_done()

    @property
    def pd_state(self):
        self.send_command('br pd state')
        return self._expect_command_output()[0].strip()

    def get_netdata_non_nat64_routes(self):
        nat64_routes = []
        routes = self.get_routes()
        for route in routes:
            if 'n' not in route.split(' ')[1]:
                nat64_routes.append(route.split(' ')[0])
        return nat64_routes

    def get_netdata_nat64_routes(self):
        nat64_routes = []
        routes = self.get_routes()
        for route in routes:
            if 'n' in route.split(' ')[1]:
                nat64_routes.append(route.split(' ')[0])
        return nat64_routes

    def get_br_nat64_prefix(self):
        cmd = 'br nat64prefix local'
        self.send_command(cmd)
        return self._expect_command_output()[0]

    def get_br_favored_nat64_prefix(self):
        cmd = 'br nat64prefix favored'
        self.send_command(cmd)
        return self._expect_command_output()[0].split(' ')[0]

    def enable_nat64(self):
        self.send_command(f'nat64 enable')
        self._expect_done()

    def disable_nat64(self):
        self.send_command(f'nat64 disable')
        self._expect_done()

    def get_nat64_state(self):
        self.send_command('nat64 state')
        res = {}
        for line in self._expect_command_output():
            state = line.split(':')
            res[state[0].strip()] = state[1].strip()
        return res

    def get_nat64_mappings(self):
        cmd = 'nat64 mappings'
        self.send_command(cmd)
        result = self._expect_command_output()
        session = None
        session_counters = None
        sessions = []

        for line in result:
            m = re.match(
                r'\|\s+([a-f0-9]+)\s+\|\s+(.+)\s+\|\s+(.+)\s+\|\s+(\d+)s\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|',
                line)
            if m:
                groups = m.groups()
                if session:
                    session['counters'] = session_counters
                    sessions.append(session)
                session = {
                    'id': groups[0],
                    'ip6': groups[1],
                    'ip4': groups[2],
                    'expiry': int(groups[3]),
                }
                session_counters = {}
                session_counters['total'] = {
                    '4to6': {
                        'packets': int(groups[4]),
                        'bytes': int(groups[5]),
                    },
                    '6to4': {
                        'packets': int(groups[6]),
                        'bytes': int(groups[7]),
                    },
                }
                continue
            if not session:
                continue
            m = re.match(r'\|\s+\|\s+(.+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|', line)
            if m:
                groups = m.groups()
                session_counters[groups[0]] = {
                    '4to6': {
                        'packets': int(groups[1]),
                        'bytes': int(groups[2]),
                    },
                    '6to4': {
                        'packets': int(groups[3]),
                        'bytes': int(groups[4]),
                    },
                }
        if session:
            session['counters'] = session_counters
            sessions.append(session)
        return sessions

    def get_nat64_counters(self):
        cmd = 'nat64 counters'
        self.send_command(cmd)
        result = self._expect_command_output()

        protocol_counters = {}
        error_counters = {}
        for line in result:
            m = re.match(r'\|\s+(.+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|', line)
            if m:
                groups = m.groups()
                protocol_counters[groups[0]] = {
                    '4to6': {
                        'packets': int(groups[1]),
                        'bytes': int(groups[2]),
                    },
                    '6to4': {
                        'packets': int(groups[3]),
                        'bytes': int(groups[4]),
                    },
                }
                continue
            m = re.match(r'\|\s+(.+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|', line)
            if m:
                groups = m.groups()
                error_counters[groups[0]] = {
                    '4to6': {
                        'packets': int(groups[1]),
                    },
                    '6to4': {
                        'packets': int(groups[2]),
                    },
                }
                continue
        return {'protocol': protocol_counters, 'errors': error_counters}

    def get_prefixes(self):
        return self.get_netdata()['Prefixes']

    def get_routes(self):
        return self.get_netdata()['Routes']

    def get_services(self):
        netdata = self.netdata_show()
        services = []
        services_section = False

        for line in netdata:
            if line.startswith('Services:'):
                services_section = True
            elif line.startswith('Contexts'):
                services_section = False
            elif services_section:
                services.append(line.strip().split(' '))
        return services

    def netdata_show(self):
        self.send_command('netdata show')
        return self._expect_command_output()

    def get_netdata(self):
        raw_netdata = self.netdata_show()
        netdata = {'Prefixes': [], 'Routes': [], 'Services': [], 'Contexts': [], 'Commissioning': []}
        key_list = ['Prefixes', 'Routes', 'Services', 'Contexts', 'Commissioning']
        key = None

        for i in range(0, len(raw_netdata)):
            keys = list(filter(raw_netdata[i].startswith, key_list))
            if keys != []:
                key = keys[0]
            elif key is not None:
                netdata[key].append(raw_netdata[i])

        return netdata

    def add_route(self, prefix, stable=False, nat64=False, prf='med'):
        cmd = 'route add %s ' % prefix
        if stable:
            cmd += 's'
        if nat64:
            cmd += 'n'
        cmd += ' %s' % prf
        self.send_command(cmd)
        self._expect_done()

    def remove_route(self, prefix):
        cmd = 'route remove %s' % prefix
        self.send_command(cmd)
        self._expect_done()

    def register_netdata(self):
        self.send_command('netdata register')
        self._expect_done()

    def netdata_publish_dnssrp_anycast(self, seqnum, version=0):
        self.send_command(f'netdata publish dnssrp anycast {seqnum} {version}')
        self._expect_done()

    def netdata_publish_dnssrp_unicast(self, address, port, version=0):
        self.send_command(f'netdata publish dnssrp unicast {address} {port} {version}')
        self._expect_done()

    def netdata_publish_dnssrp_unicast_mleid(self, port, version=0):
        self.send_command(f'netdata publish dnssrp unicast {port} {version}')
        self._expect_done()

    def netdata_unpublish_dnssrp(self):
        self.send_command('netdata unpublish dnssrp')
        self._expect_done()

    def netdata_publish_prefix(self, prefix, flags='paosr', prf='med'):
        self.send_command(f'netdata publish prefix {prefix} {flags} {prf}')
        self._expect_done()

    def netdata_publish_route(self, prefix, flags='s', prf='med'):
        self.send_command(f'netdata publish route {prefix} {flags} {prf}')
        self._expect_done()

    def netdata_publish_replace(self, old_prefix, prefix, flags='s', prf='med'):
        self.send_command(f'netdata publish replace {old_prefix} {prefix} {flags} {prf}')
        self._expect_done()

    def netdata_unpublish_prefix(self, prefix):
        self.send_command(f'netdata unpublish {prefix}')
        self._expect_done()

    def send_network_diag_get(self, addr, tlv_types):
        self.send_command('networkdiagnostic get %s %s' % (addr, ' '.join([str(t.value) for t in tlv_types])))

        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(8)
            timeout = 1
        else:
            timeout = 8

        self._expect_done(timeout=timeout)

    def send_network_diag_reset(self, addr, tlv_types):
        self.send_command('networkdiagnostic reset %s %s' % (addr, ' '.join([str(t.value) for t in tlv_types])))

        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(8)
            timeout = 1
        else:
            timeout = 8

        self._expect_done(timeout=timeout)

    def energy_scan(self, mask, count, period, scan_duration, ipaddr):
        cmd = 'commissioner energy %d %d %d %d %s' % (
            mask,
            count,
            period,
            scan_duration,
            ipaddr,
        )
        self.send_command(cmd)

        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(8)
            timeout = 1
        else:
            timeout = 8

        self._expect('Energy:', timeout=timeout)

    def panid_query(self, panid, mask, ipaddr):
        cmd = 'commissioner panid %d %d %s' % (panid, mask, ipaddr)
        self.send_command(cmd)

        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(8)
            timeout = 1
        else:
            timeout = 8

        self._expect('Conflict:', timeout=timeout)

    def scan(self, result=1, timeout=10):
        self.send_command('scan')

        self.simulator.go(timeout)

        if result == 1:
            networks = []
            for line in self._expect_command_output()[2:]:
                _, panid, extaddr, channel, dbm, lqi, _ = map(str.strip, line.split('|'))
                panid = int(panid, 16)
                channel, dbm, lqi = map(int, (channel, dbm, lqi))

                networks.append({
                    'panid': panid,
                    'extaddr': extaddr,
                    'channel': channel,
                    'dbm': dbm,
                    'lqi': lqi,
                })
            return networks

    def scan_energy(self, timeout=10):
        self.send_command('scan energy')
        self.simulator.go(timeout)
        rssi_list = []
        for line in self._expect_command_output()[2:]:
            _, channel, rssi, _ = line.split('|')
            rssi_list.append({
                'channel': int(channel.strip()),
                'rssi': int(rssi.strip()),
            })
        return rssi_list

    def ping(self, ipaddr, num_responses=1, size=8, timeout=5, count=1, interval=1, hoplimit=64, interface=None):
        args = f'{ipaddr} {size} {count} {interval} {hoplimit} {timeout}'
        if interface is not None:
            args = f'-I {interface} {args}'
        cmd = f'ping {args}'

        self.send_command(cmd)

        wait_allowance = 3
        end = self.simulator.now() + timeout + wait_allowance

        responders = {}

        result = True
        # ncp-sim doesn't print Done
        done = (self.node_type == 'ncp-sim')
        while len(responders) < num_responses or not done:
            self.simulator.go(1)
            try:
                i = self._expect([r'from (\S+):', r'Done'], timeout=0.1)
            except (pexpect.TIMEOUT, socket.timeout):
                if self.simulator.now() < end:
                    continue
                result = False
                if isinstance(self.simulator, simulator.VirtualTime):
                    self.simulator.sync_devices()
                break
            else:
                if i == 0:
                    responders[self.pexpect.match.groups()[0]] = 1
                elif i == 1:
                    done = True
        return result

    def reset(self):
        self._reset('reset')

    def factory_reset(self):
        self._reset('factoryreset')

    def _reset(self, cmd):
        self.send_command(cmd, expect_command_echo=False, maybeoff=True)
        time.sleep(self.RESET_DELAY)
        # Send a "version" command and drain the CLI output after reset
        self.send_command('version', expect_command_echo=False)
        while True:
            try:
                self._expect(r"[^\n]+\n", timeout=0.1)
                continue
            except pexpect.TIMEOUT:
                break

        if self.is_otbr:
            self.set_log_level(5)

    def set_router_selection_jitter(self, jitter):
        cmd = 'routerselectionjitter %d' % jitter
        self.send_command(cmd)
        self._expect_done()

    def set_active_dataset(
        self,
        timestamp=None,
        channel=None,
        channel_mask=None,
        extended_panid=None,
        mesh_local_prefix=None,
        network_key=None,
        network_name=None,
        panid=None,
        pskc=None,
        security_policy=[],
        updateExisting=False,
    ):

        if updateExisting:
            self.send_command('dataset init active', go=False)
        else:
            self.send_command('dataset clear', go=False)
        self._expect_done()

        if timestamp is not None:
            cmd = 'dataset activetimestamp %d' % timestamp
            self.send_command(cmd, go=False)
            self._expect_done()

        if channel is not None:
            cmd = 'dataset channel %d' % channel
            self.send_command(cmd, go=False)
            self._expect_done()

        if channel_mask is not None:
            cmd = 'dataset channelmask %d' % channel_mask
            self.send_command(cmd, go=False)
            self._expect_done()

        if extended_panid is not None:
            cmd = 'dataset extpanid %s' % extended_panid
            self.send_command(cmd, go=False)
            self._expect_done()

        if mesh_local_prefix is not None:
            cmd = 'dataset meshlocalprefix %s' % mesh_local_prefix
            self.send_command(cmd, go=False)
            self._expect_done()

        if network_key is not None:
            cmd = 'dataset networkkey %s' % network_key
            self.send_command(cmd, go=False)
            self._expect_done()
            self.simulator.add_network_key(network_key)

        if network_name is not None:
            cmd = 'dataset networkname %s' % network_name
            self.send_command(cmd, go=False)
            self._expect_done()

        if panid is not None:
            cmd = 'dataset panid %d' % panid
            self.send_command(cmd, go=False)
            self._expect_done()

        if pskc is not None:
            cmd = 'dataset pskc %s' % pskc
            self.send_command(cmd, go=False)
            self._expect_done()

        if security_policy is not None:
            if len(security_policy) >= 2:
                cmd = 'dataset securitypolicy %s %s' % (
                    str(security_policy[0]),
                    security_policy[1],
                )
            if len(security_policy) >= 3:
                cmd += ' %s' % (str(security_policy[2]))
            self.send_command(cmd, go=False)
            self._expect_done()

        self.send_command('dataset commit active', go=False)
        self._expect_done()

    def set_pending_dataset(self, pendingtimestamp, activetimestamp, panid=None, channel=None, delay=None):
        self.send_command('dataset clear')
        self._expect_done()

        cmd = 'dataset pendingtimestamp %d' % pendingtimestamp
        self.send_command(cmd)
        self._expect_done()

        cmd = 'dataset activetimestamp %d' % activetimestamp
        self.send_command(cmd)
        self._expect_done()

        if panid is not None:
            cmd = 'dataset panid %d' % panid
            self.send_command(cmd)
            self._expect_done()

        if channel is not None:
            cmd = 'dataset channel %d' % channel
            self.send_command(cmd)
            self._expect_done()

        if delay is not None:
            cmd = 'dataset delay %d' % delay
            self.send_command(cmd)
            self._expect_done()

        # Set the meshlocal prefix in config.py
        self.send_command('dataset meshlocalprefix %s' % config.MESH_LOCAL_PREFIX.split('/')[0])
        self._expect_done()

        self.send_command('dataset commit pending')
        self._expect_done()

    def start_dataset_updater(self, panid=None, channel=None, security_policy=None, delay=None):
        self.send_command('dataset clear')
        self._expect_done()

        if panid is not None:
            cmd = 'dataset panid %d' % panid
            self.send_command(cmd)
            self._expect_done()

        if channel is not None:
            cmd = 'dataset channel %d' % channel
            self.send_command(cmd)
            self._expect_done()

        if security_policy is not None:
            cmd = 'dataset securitypolicy %d %s ' % (security_policy[0], security_policy[1])
            if (len(security_policy) >= 3):
                cmd += '%d ' % (security_policy[2])
            self.send_command(cmd)
            self._expect_done()

        if delay is not None:
            cmd = 'dataset delay %d ' % delay
            self.send_command(cmd)
            self._expect_done()

        self.send_command('dataset updater start')
        self._expect_done()

    def announce_begin(self, mask, count, period, ipaddr):
        cmd = 'commissioner announce %d %d %d %s' % (
            mask,
            count,
            period,
            ipaddr,
        )
        self.send_command(cmd)
        self._expect_done()

    def send_mgmt_active_set(
        self,
        active_timestamp=None,
        channel=None,
        channel_mask=None,
        extended_panid=None,
        panid=None,
        network_key=None,
        mesh_local=None,
        network_name=None,
        security_policy=None,
        binary=None,
    ):
        cmd = 'dataset mgmtsetcommand active '

        if active_timestamp is not None:
            cmd += 'activetimestamp %d ' % active_timestamp

        if channel is not None:
            cmd += 'channel %d ' % channel

        if channel_mask is not None:
            cmd += 'channelmask %d ' % channel_mask

        if extended_panid is not None:
            cmd += 'extpanid %s ' % extended_panid

        if panid is not None:
            cmd += 'panid %d ' % panid

        if network_key is not None:
            cmd += 'networkkey %s ' % network_key
            self.simulator.add_network_key(network_key)

        if mesh_local is not None:
            cmd += 'localprefix %s ' % mesh_local

        if network_name is not None:
            cmd += 'networkname %s ' % self._escape_escapable(network_name)

        if security_policy is not None:
            cmd += 'securitypolicy %d %s ' % (security_policy[0], security_policy[1])
            if (len(security_policy) >= 3):
                cmd += '%d ' % (security_policy[2])

        if binary is not None:
            cmd += '-x %s ' % binary

        self.send_command(cmd)
        self._expect_done()

    def send_mgmt_active_get(self, addr='', tlvs=[]):
        cmd = 'dataset mgmtgetcommand active'

        if addr != '':
            cmd += ' address '
            cmd += addr

        if len(tlvs) != 0:
            tlv_str = ''.join('%02x' % tlv for tlv in tlvs)
            cmd += ' -x '
            cmd += tlv_str

        self.send_command(cmd)
        self._expect_done()

    def send_mgmt_pending_get(self, addr='', tlvs=[]):
        cmd = 'dataset mgmtgetcommand pending'

        if addr != '':
            cmd += ' address '
            cmd += addr

        if len(tlvs) != 0:
            tlv_str = ''.join('%02x' % tlv for tlv in tlvs)
            cmd += ' -x '
            cmd += tlv_str

        self.send_command(cmd)
        self._expect_done()

    def send_mgmt_pending_set(
        self,
        pending_timestamp=None,
        active_timestamp=None,
        delay_timer=None,
        channel=None,
        panid=None,
        network_key=None,
        mesh_local=None,
        network_name=None,
    ):
        cmd = 'dataset mgmtsetcommand pending '
        if pending_timestamp is not None:
            cmd += 'pendingtimestamp %d ' % pending_timestamp

        if active_timestamp is not None:
            cmd += 'activetimestamp %d ' % active_timestamp

        if delay_timer is not None:
            cmd += 'delaytimer %d ' % delay_timer

        if channel is not None:
            cmd += 'channel %d ' % channel

        if panid is not None:
            cmd += 'panid %d ' % panid

        if network_key is not None:
            cmd += 'networkkey %s ' % network_key
            self.simulator.add_network_key(network_key)

        if mesh_local is not None:
            cmd += 'localprefix %s ' % mesh_local

        if network_name is not None:
            cmd += 'networkname %s ' % self._escape_escapable(network_name)

        self.send_command(cmd)
        self._expect_done()

    def coap_cancel(self):
        """
        Cancel a CoAP subscription.
        """
        cmd = 'coap cancel'
        self.send_command(cmd)
        self._expect_done()

    def coap_delete(self, ipaddr, uri, con=False, payload=None):
        """
        Send a DELETE request via CoAP.
        """
        return self._coap_rq('delete', ipaddr, uri, con, payload)

    def coap_get(self, ipaddr, uri, con=False, payload=None):
        """
        Send a GET request via CoAP.
        """
        return self._coap_rq('get', ipaddr, uri, con, payload)

    def coap_get_block(self, ipaddr, uri, size=16, count=0):
        """
        Send a GET request via CoAP.
        """
        return self._coap_rq_block('get', ipaddr, uri, size, count)

    def coap_observe(self, ipaddr, uri, con=False, payload=None):
        """
        Send a GET request via CoAP with Observe set.
        """
        return self._coap_rq('observe', ipaddr, uri, con, payload)

    def coap_post(self, ipaddr, uri, con=False, payload=None):
        """
        Send a POST request via CoAP.
        """
        return self._coap_rq('post', ipaddr, uri, con, payload)

    def coap_post_block(self, ipaddr, uri, size=16, count=0):
        """
        Send a POST request via CoAP.
        """
        return self._coap_rq_block('post', ipaddr, uri, size, count)

    def coap_put(self, ipaddr, uri, con=False, payload=None):
        """
        Send a PUT request via CoAP.
        """
        return self._coap_rq('put', ipaddr, uri, con, payload)

    def coap_put_block(self, ipaddr, uri, size=16, count=0):
        """
        Send a PUT request via CoAP.
        """
        return self._coap_rq_block('put', ipaddr, uri, size, count)

    def _coap_rq(self, method, ipaddr, uri, con=False, payload=None):
        """
        Issue a GET/POST/PUT/DELETE/GET OBSERVE request.
        """
        cmd = 'coap %s %s %s' % (method, ipaddr, uri)
        if con:
            cmd += ' con'
        else:
            cmd += ' non'

        if payload is not None:
            cmd += ' %s' % payload

        self.send_command(cmd)
        return self.coap_wait_response()

    def _coap_rq_block(self, method, ipaddr, uri, size=16, count=0):
        """
        Issue a GET/POST/PUT/DELETE/GET OBSERVE BLOCK request.
        """
        cmd = 'coap %s %s %s' % (method, ipaddr, uri)

        cmd += ' block-%d' % size

        if count != 0:
            cmd += ' %d' % count

        self.send_command(cmd)
        return self.coap_wait_response()

    def coap_wait_response(self):
        """
        Wait for a CoAP response, and return it.
        """
        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(5)
            timeout = 1
        else:
            timeout = 5

        self._expect(r'coap response from ([\da-f:]+)(?: OBS=(\d+))?'
                     r'(?: with payload: ([\da-f]+))?\b',
                     timeout=timeout)
        (source, observe, payload) = self.pexpect.match.groups()
        source = source.decode('UTF-8')

        if observe is not None:
            observe = int(observe, base=10)

        if payload is not None:
            try:
                payload = binascii.a2b_hex(payload).decode('UTF-8')
            except UnicodeDecodeError:
                pass

        # Return the values received
        return dict(source=source, observe=observe, payload=payload)

    def coap_wait_request(self):
        """
        Wait for a CoAP request to be made.
        """
        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(5)
            timeout = 1
        else:
            timeout = 5

        self._expect(r'coap request from ([\da-f:]+)(?: OBS=(\d+))?'
                     r'(?: with payload: ([\da-f]+))?\b',
                     timeout=timeout)
        (source, observe, payload) = self.pexpect.match.groups()
        source = source.decode('UTF-8')

        if observe is not None:
            observe = int(observe, base=10)

        if payload is not None:
            payload = binascii.a2b_hex(payload).decode('UTF-8')

        # Return the values received
        return dict(source=source, observe=observe, payload=payload)

    def coap_wait_subscribe(self):
        """
        Wait for a CoAP client to be subscribed.
        """
        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(5)
            timeout = 1
        else:
            timeout = 5

        self._expect(r'Subscribing client\b', timeout=timeout)

    def coap_wait_ack(self):
        """
        Wait for a CoAP notification ACK.
        """
        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(5)
            timeout = 1
        else:
            timeout = 5

        self._expect(r'Received ACK in reply to notification from ([\da-f:]+)\b', timeout=timeout)
        (source,) = self.pexpect.match.groups()
        source = source.decode('UTF-8')

        return source

    def coap_set_resource_path(self, path):
        """
        Set the path for the CoAP resource.
        """
        cmd = 'coap resource %s' % path
        self.send_command(cmd)
        self._expect_done()

    def coap_set_resource_path_block(self, path, count=0):
        """
        Set the path for the CoAP resource and how many blocks can be received from this resource.
        """
        cmd = 'coap resource %s %d' % (path, count)
        self.send_command(cmd)
        self._expect('Done')

    def coap_set_content(self, content):
        """
        Set the content of the CoAP resource.
        """
        cmd = 'coap set %s' % content
        self.send_command(cmd)
        self._expect_done()

    def coap_start(self):
        """
        Start the CoAP service.
        """
        cmd = 'coap start'
        self.send_command(cmd)
        self._expect_done()

    def coap_stop(self):
        """
        Stop the CoAP service.
        """
        cmd = 'coap stop'
        self.send_command(cmd)

        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(5)
            timeout = 1
        else:
            timeout = 5

        self._expect_done(timeout=timeout)

    def coaps_start_psk(self, psk, pskIdentity):
        cmd = 'coaps psk %s %s' % (psk, pskIdentity)
        self.send_command(cmd)
        self._expect_done()

        cmd = 'coaps start'
        self.send_command(cmd)
        self._expect_done()

    def coaps_start_x509(self):
        cmd = 'coaps x509'
        self.send_command(cmd)
        self._expect_done()

        cmd = 'coaps start'
        self.send_command(cmd)
        self._expect_done()

    def coaps_set_resource_path(self, path):
        cmd = 'coaps resource %s' % path
        self.send_command(cmd)
        self._expect_done()

    def coaps_stop(self):
        cmd = 'coaps stop'
        self.send_command(cmd)

        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(5)
            timeout = 1
        else:
            timeout = 5

        self._expect_done(timeout=timeout)

    def coaps_connect(self, ipaddr):
        cmd = 'coaps connect %s' % ipaddr
        self.send_command(cmd)

        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(5)
            timeout = 1
        else:
            timeout = 5

        self._expect('coaps connected', timeout=timeout)

    def coaps_disconnect(self):
        cmd = 'coaps disconnect'
        self.send_command(cmd)
        self._expect_done()
        self.simulator.go(5)

    def coaps_get(self):
        cmd = 'coaps get test'
        self.send_command(cmd)

        if isinstance(self.simulator, simulator.VirtualTime):
            self.simulator.go(5)
            timeout = 1
        else:
            timeout = 5

        self._expect('coaps response', timeout=timeout)

    def commissioner_mgmtget(self, tlvs_binary=None):
        cmd = 'commissioner mgmtget'
        if tlvs_binary is not None:
            cmd += ' -x %s' % tlvs_binary
        self.send_command(cmd)
        self._expect_done()

    def commissioner_mgmtset(self, tlvs_binary):
        cmd = 'commissioner mgmtset -x %s' % tlvs_binary
        self.send_command(cmd)
        self._expect_done()

    def bytes_to_hex_str(self, src):
        return ''.join(format(x, '02x') for x in src)

    def commissioner_mgmtset_with_tlvs(self, tlvs):
        payload = bytearray()
        for tlv in tlvs:
            payload += tlv.to_hex()
        self.commissioner_mgmtset(self.bytes_to_hex_str(payload))

    def udp_start(self, local_ipaddr, local_port, bind_unspecified=False):
        cmd = 'udp open'
        self.send_command(cmd)
        self._expect_done()

        cmd = 'udp bind %s %s %s' % ("-u" if bind_unspecified else "", local_ipaddr, local_port)
        self.send_command(cmd)
        self._expect_done()

    def udp_stop(self):
        cmd = 'udp close'
        self.send_command(cmd)
        self._expect_done()

    def udp_send(self, bytes, ipaddr, port, success=True):
        cmd = 'udp send %s %d -s %d ' % (ipaddr, port, bytes)
        self.send_command(cmd)
        if success:
            self._expect_done()
        else:
            self._expect('Error')

    def udp_check_rx(self, bytes_should_rx):
        self._expect('%d bytes' % bytes_should_rx)

    def set_routereligible(self, enable: bool):
        cmd = f'routereligible {"enable" if enable else "disable"}'
        self.send_command(cmd)
        self._expect_done()

    def router_list(self):
        cmd = 'router list'
        self.send_command(cmd)
        self._expect([r'(\d+)((\s\d+)*)'])

        g = self.pexpect.match.groups()
        router_list = g[0].decode('utf8') + ' ' + g[1].decode('utf8')
        router_list = [int(x) for x in router_list.split()]
        self._expect_done()
        return router_list

    def router_table(self):
        cmd = 'router table'
        self.send_command(cmd)

        self._expect(r'(.*)Done')
        g = self.pexpect.match.groups()
        output = g[0].decode('utf8')
        lines = output.strip().split('\n')
        lines = [l.strip() for l in lines]
        router_table = {}
        for i, line in enumerate(lines):
            if not line.startswith('|') or not line.endswith('|'):
                if i not in (0, 2):
                    # should not happen
                    print("unexpected line %d: %s" % (i, line))

                continue

            line = line[1:][:-1]
            line = [x.strip() for x in line.split('|')]
            if len(line) < 9:
                print("unexpected line %d: %s" % (i, line))
                continue

            try:
                int(line[0])
            except ValueError:
                if i != 1:
                    print("unexpected line %d: %s" % (i, line))
                continue

            id = int(line[0])
            rloc16 = int(line[1], 16)
            nexthop = int(line[2])
            pathcost = int(line[3])
            lqin = int(line[4])
            lqout = int(line[5])
            age = int(line[6])
            emac = str(line[7])
            link = int(line[8])

            router_table[id] = {
                'rloc16': rloc16,
                'nexthop': nexthop,
                'pathcost': pathcost,
                'lqin': lqin,
                'lqout': lqout,
                'age': age,
                'emac': emac,
                'link': link,
            }

        return router_table

    def link_metrics_request_single_probe(self, dst_addr: str, linkmetrics_flags: str, mode: str = ''):
        cmd = 'linkmetrics request %s %s single %s' % (mode, dst_addr, linkmetrics_flags)
        self.send_command(cmd)
        self.simulator.go(5)
        return self._parse_linkmetrics_query_result(self._expect_command_output())

    def link_metrics_request_forward_tracking_series(self, dst_addr: str, series_id: int, mode: str = ''):
        cmd = 'linkmetrics request %s %s forward %d' % (mode, dst_addr, series_id)
        self.send_command(cmd)
        self.simulator.go(5)
        return self._parse_linkmetrics_query_result(self._expect_command_output())

    def _parse_linkmetrics_query_result(self, lines):
        """Parse link metrics query result"""

        # Example of command output:
        # ['Received Link Metrics Report from: fe80:0:0:0:146e:a00:0:1',
        #  '- PDU Counter: 1 (Count/Summation)',
        #  '- LQI: 0 (Exponential Moving Average)',
        #  '- Margin: 80 (dB) (Exponential Moving Average)',
        #  '- RSSI: -20 (dBm) (Exponential Moving Average)']
        #
        # Or 'Link Metrics Report, status: {status}'

        result = {}
        for line in lines:
            if line.startswith('- '):
                k, v = line[2:].split(': ')
                result[k] = v.split(' ')[0]
            elif line.startswith('Link Metrics Report, status: '):
                result['Status'] = line[29:]
        return result

    def link_metrics_config_req_enhanced_ack_based_probing(self,
                                                           dst_addr: str,
                                                           enable: bool,
                                                           metrics_flags: str,
                                                           ext_flags='',
                                                           mode: str = ''):
        cmd = "linkmetrics config %s %s enhanced-ack" % (mode, dst_addr)
        if enable:
            cmd = cmd + (" register %s %s" % (metrics_flags, ext_flags))
        else:
            cmd = cmd + " clear"
        self.send_command(cmd)
        self._expect_done()

    def link_metrics_config_req_forward_tracking_series(self,
                                                        dst_addr: str,
                                                        series_id: int,
                                                        series_flags: str,
                                                        metrics_flags: str,
                                                        mode: str = ''):
        cmd = "linkmetrics config %s %s forward %d %s %s" % (mode, dst_addr, series_id, series_flags, metrics_flags)
        self.send_command(cmd)
        self._expect_done()

    def link_metrics_send_link_probe(self, dst_addr: str, series_id: int, length: int):
        cmd = "linkmetrics probe %s %d %d" % (dst_addr, series_id, length)
        self.send_command(cmd)
        self._expect_done()

    def link_metrics_mgr_set_enabled(self, enable: bool):
        op_str = "enable" if enable else "disable"
        cmd = f'linkmetricsmgr {op_str}'
        self.send_command(cmd)
        self._expect_done()

    def send_address_notification(self, dst: str, target: str, mliid: str):
        cmd = f'fake /a/an {dst} {target} {mliid}'
        self.send_command(cmd)
        self._expect_done()

    def send_proactive_backbone_notification(self, target: str, mliid: str, ltt: int):
        cmd = f'fake /b/ba {target} {mliid} {ltt}'
        self.send_command(cmd)
        self._expect_done()

    def dns_get_config(self):
        """
        Returns the DNS config as a list of property dictionary (string key and string value).

        Example output:
        {
            'Server': '[fd00:0:0:0:0:0:0:1]:1234'
            'ResponseTimeout': '5000 ms'
            'MaxTxAttempts': '2'
            'RecursionDesired': 'no'
        }
        """
        cmd = f'dns config'
        self.send_command(cmd)
        output = self._expect_command_output()
        config = {}
        for line in output:
            k, v = line.split(': ')
            config[k] = v
        return config

    def dns_set_config(self, config):
        cmd = f'dns config {config}'
        self.send_command(cmd)
        self._expect_done()

    def dns_resolve(self, hostname, server=None, port=53):
        cmd = f'dns resolve {hostname}'
        if server is not None:
            cmd += f' {server} {port}'

        self.send_command(cmd)
        self.simulator.go(10)
        output = self._expect_command_output()
        dns_resp = output[0]
        # example output: "DNS response for host1.default.service.arpa. - fd00:db8:0:0:fd3d:d471:1e8c:b60 TTL:7190 "
        #                 " fd00:db8:0:0:0:ff:fe00:9000 TTL:7190"
        addrs = dns_resp.strip().split(' - ')[1].split(' ')
        ip = [item.strip() for item in addrs[::2]]
        ttl = [int(item.split('TTL:')[1]) for item in addrs[1::2]]

        return list(zip(ip, ttl))

    def _parse_dns_service_info(self, output):
        # Example of `output`
        #   Port:22222, Priority:2, Weight:2, TTL:7155
        #   Host:host2.default.service.arpa.
        #   HostAddress:0:0:0:0:0:0:0:0 TTL:0
        #   TXT:[a=00, b=02bb] TTL:7155

        m = re.match(
            r'.*Port:(\d+), Priority:(\d+), Weight:(\d+), TTL:(\d+)\s+Host:(.*?)\s+HostAddress:(\S+) TTL:(\d+)\s+TXT:\[(.*?)\] TTL:(\d+)',
            '\r'.join(output))
        if not m:
            return {}
        port, priority, weight, srv_ttl, hostname, address, aaaa_ttl, txt_data, txt_ttl = m.groups()
        return {
            'port': int(port),
            'priority': int(priority),
            'weight': int(weight),
            'host': hostname,
            'address': address,
            'txt_data': txt_data,
            'srv_ttl': int(srv_ttl),
            'txt_ttl': int(txt_ttl),
            'aaaa_ttl': int(aaaa_ttl),
        }

    def dns_resolve_service(self, instance, service, server=None, port=53):
        """
        Resolves the service instance and returns the instance information as a dict.

        Example return value:
            {
                'port': 12345,
                'priority': 0,
                'weight': 0,
                'host': 'ins1._ipps._tcp.default.service.arpa.',
                'address': '2001::1',
                'txt_data': 'a=00, b=02bb',
                'srv_ttl': 7100,
                'txt_ttl': 7100,
                'aaaa_ttl': 7100,
            }
        """
        instance = self._escape_escapable(instance)
        cmd = f'dns service {instance} {service}'
        if server is not None:
            cmd += f' {server} {port}'

        self.send_command(cmd)
        self.simulator.go(10)
        output = self._expect_command_output()
        info = self._parse_dns_service_info(output)
        if not info:
            raise Exception('dns resolve service failed: %s.%s' % (instance, service))
        return info

    @staticmethod
    def __parse_hex_string(hexstr: str) -> bytes:
        assert (len(hexstr) % 2 == 0)
        return bytes(int(hexstr[i:i + 2], 16) for i in range(0, len(hexstr), 2))

    def dns_browse(self, service_name, server=None, port=53):
        """
        Browse the service and returns the instances.

        Example return value:
            {
                'ins1': {
                    'port': 12345,
                    'priority': 1,
                    'weight': 1,
                    'host': 'ins1._ipps._tcp.default.service.arpa.',
                    'address': '2001::1',
                    'txt_data': 'a=00, b=11cf',
                    'srv_ttl': 7100,
                    'txt_ttl': 7100,
                    'aaaa_ttl': 7100,
                },
                'ins2': {
                    'port': 12345,
                    'priority': 2,
                    'weight': 2,
                    'host': 'ins2._ipps._tcp.default.service.arpa.',
                    'address': '2001::2',
                    'txt_data': 'a=01, b=23dd',
                    'srv_ttl': 7100,
                    'txt_ttl': 7100,
                    'aaaa_ttl': 7100,
                }
            }
        """
        cmd = f'dns browse {service_name}'
        if server is not None:
            cmd += f' {server} {port}'

        self.send_command(cmd)
        self.simulator.go(10)
        output = self._expect_command_output()

        # Example output:
        # DNS browse response for _ipps._tcp.default.service.arpa.
        # ins2
        #     Port:22222, Priority:2, Weight:2, TTL:7175
        #     Host:host2.default.service.arpa.
        #     HostAddress:fd00:db8:0:0:3205:28dd:5b87:6a63 TTL:7175
        #     TXT:[a=00, b=11cf] TTL:7175
        # ins1
        #     Port:11111, Priority:1, Weight:1, TTL:7170
        #     Host:host1.default.service.arpa.
        #     HostAddress:fd00:db8:0:0:39f4:d9:eb4f:778 TTL:7170
        #     TXT:[a=01, b=23dd] TTL:7170
        # Done

        result = {}
        index = 1  # skip first line
        while index < len(output):
            ins = output[index].strip()
            result[ins] = self._parse_dns_service_info(output[index + 1:index + 6])
            index = index + (5 if result[ins] else 1)
        return result

    def dns_query(self, rrtype, first_label, next_labels, server=None, port=53):
        """
        Send a DNS query for a given record type and name.

        Output is an array of records (as dictionary) with string keys and values.
        [
           {'RecordType': '25',
           'RecordLength': '78',
           'TTL': '7105',
           'Section': 'answer',
           'Name': 'ins1._IPPS._TCP.DEFAULT.SERVICE.ARPA.',
           'RecordData': '[001900010000a0610...d45d3]'
           }
        ]
        """
        cmd = f'dns query {rrtype} {first_label} {next_labels}'
        if server is not None:
            cmd += f' {server} {port}'

        self.send_command(cmd)
        self.simulator.go(10)
        output = self._expect_command_output()

        # Example output:
        # DNS query response for ins1._IPPS._TCP.DEFAULT.SERVICE.ARPA.
        # 0)
        #   RecordType:25, RecordLength:78, TTL:7105, Section:answer
        #   Name:ins1._IPPS._TCP.DEFAULT.SERVICE.ARPA.
        #   RecordData:[00190001000...cdb]
        # Done

        result = []
        index = 1  # Skip first line
        while (index < len(output)):
            if (index > len(output) - 4):
                break
            record = {}
            for line in output[index + 1:index + 4]:
                for item in line.strip().split(','):
                    k, v = item.split(':')
                    record[k.strip()] = v.strip()
            result.append(record)
            index += 4

        return result

    def set_mliid(self, mliid: str):
        cmd = f'mliid {mliid}'
        self.send_command(cmd)
        self._expect_command_output()

    def history_netinfo(self, num_entries=0):
        """
        Get the `netinfo` history list, parse each entry and return
        a list of dictionary (string key and string value) entries.

        Example of return value:
        [
            {
                'age': '00:00:00.000 ago',
                'role': 'disabled',
                'mode': 'rdn',
                'rloc16': '0x7400',
                'partition-id': '1318093703'
            },
            {
                'age': '00:00:02.588 ago',
                'role': 'leader',
                'mode': 'rdn',
                'rloc16': '0x7400',
                'partition-id': '1318093703'
            }
        ]
        """
        cmd = f'history netinfo list {num_entries}'
        self.send_command(cmd)
        output = self._expect_command_output()
        netinfos = []
        for entry in output:
            netinfo = {}
            age, info = entry.split(' -> ')
            netinfo['age'] = age
            for item in info.split(' '):
                k, v = item.split(':')
                netinfo[k] = v
            netinfos.append(netinfo)
        return netinfos

    def history_rx(self, num_entries=0):
        """
        Get the IPv6 RX history list, parse each entry and return
        a list of dictionary (string key and string value) entries.

        Example of return value:
        [
            {
                'age': '00:00:01.999',
                'type': 'ICMP6(EchoReqst)',
                'len': '16',
                'sec': 'yes',
                'prio': 'norm',
                'rss': '-20',
                'from': '0xac00',
                'radio': '15.4',
                'src': '[fd00:db8:0:0:2cfa:fd61:58a9:f0aa]:0',
                'dst': '[fd00:db8:0:0:ed7e:2d04:e543:eba5]:0',
            }
        ]
        """
        cmd = f'history rx list {num_entries}'
        self.send_command(cmd)
        return self._parse_history_rx_tx_ouput(self._expect_command_output())

    def history_tx(self, num_entries=0):
        """
        Get the IPv6 TX history list, parse each entry and return
        a list of dictionary (string key and string value) entries.

        Example of return value:
        [
            {
                'age': '00:00:01.999',
                'type': 'ICMP6(EchoReply)',
                'len': '16',
                'sec': 'yes',
                'prio': 'norm',
                'to': '0xac00',
                'tx-success': 'yes',
                'radio': '15.4',
                'src': '[fd00:db8:0:0:ed7e:2d04:e543:eba5]:0',
                'dst': '[fd00:db8:0:0:2cfa:fd61:58a9:f0aa]:0',

            }
        ]
        """
        cmd = f'history tx list {num_entries}'
        self.send_command(cmd)
        return self._parse_history_rx_tx_ouput(self._expect_command_output())

    def _parse_history_rx_tx_ouput(self, lines):
        rxtx_list = []
        for line in lines:
            if line.strip().startswith('type:'):
                for item in line.strip().split(' '):
                    k, v = item.split(':')
                    entry[k] = v
            elif line.strip().startswith('src:'):
                entry['src'] = line[4:]
            elif line.strip().startswith('dst:'):
                entry['dst'] = line[4:]
                rxtx_list.append(entry)
            else:
                entry = {}
                entry['age'] = line

        return rxtx_list

    def set_router_id_range(self, min_router_id: int, max_router_id: int):
        cmd = f'routeridrange {min_router_id} {max_router_id}'
        self.send_command(cmd)
        self._expect_command_output()

    def get_router_id_range(self):
        cmd = 'routeridrange'
        self.send_command(cmd)
        line = self._expect_command_output()[0]
        return [int(item) for item in line.split()]

    def get_channel_monitor_info(self) -> Dict:
        """
        Returns:
            Dict of channel monitor info, e.g. 
                {'enabled': '1',
                 'interval': '41000',
                 'threshold': '-75',
                 'window': '960',
                 'count': '985',
                 'occupancies': {
                    '11': '0.00%',
                    '12': '3.50%',
                    '13': '9.89%',
                    '14': '15.36%',
                    '15': '20.02%',
                    '16': '21.95%',
                    '17': '32.71%',
                    '18': '35.76%',
                    '19': '37.97%',
                    '20': '43.68%',
                    '21': '48.95%',
                    '22': '54.05%',
                    '23': '58.65%',
                    '24': '68.26%',
                    '25': '66.73%',
                    '26': '73.12%'
                    }
                }
        """
        config = {}
        self.send_command('channel monitor')

        for line in self._expect_results(r'\S+'):
            if re.match(r'.*:\s.*', line):
                key, val = line.split(':')
                config.update({key: val.strip()})
            elif re.match(r'.*:', line):  # occupancy
                occ_key, val = line.split(':')
                val = {}
                config.update({occ_key: val})
            elif 'busy' in line:
                # channel occupancies
                key = line.split()[1]
                val = line.split()[3]
                config[occ_key].update({key: val})
        return config

    def set_channel_manager_auto_enable(self, enable: bool):
        self.send_command(f'channel manager auto {int(enable)}')
        self._expect_done()

    def set_channel_manager_autocsl_enable(self, enable: bool):
        self.send_command(f'channel manager autocsl {int(enable)}')
        self._expect_done()

    def set_channel_manager_supported(self, channel_mask: int):
        self.send_command(f'channel manager supported {int(channel_mask)}')
        self._expect_done()

    def set_channel_manager_favored(self, channel_mask: int):
        self.send_command(f'channel manager favored {int(channel_mask)}')
        self._expect_done()

    def set_channel_manager_interval(self, interval: int):
        self.send_command(f'channel manager interval {interval}')
        self._expect_done()

    def set_channel_manager_cca_threshold(self, hex_value: str):
        self.send_command(f'channel manager threshold {hex_value}')
        self._expect_done()

    def get_channel_manager_config(self):
        self.send_command('channel manager')
        return self._expect_key_value_pairs(r'\S+')


class Node(NodeImpl, OtCli):
    pass


class LinuxHost():
    PING_RESPONSE_PATTERN = re.compile(r'\d+ bytes from .*:.*')
    ETH_DEV = config.BACKBONE_IFNAME

    def enable_ether(self):
        """Enable the ethernet interface.
        """

        self.bash(f'ip link set {self.ETH_DEV} up')

    def disable_ether(self):
        """Disable the ethernet interface.
        """

        self.bash(f'ip link set {self.ETH_DEV} down')

    def get_ether_addrs(self, ipv4=False, ipv6=True):
        output = self.bash(f'ip addr list dev {self.ETH_DEV}')

        addrs = []
        for line in output:
            # line examples:
            # "inet6 fe80::42:c0ff:fea8:903/64 scope link"
            # "inet 192.168.9.1/24 brd 192.168.9.255 scope global eth0"
            line = line.strip().split()

            if not line or not line[0].startswith('inet'):
                continue
            if line[0] == 'inet' and not ipv4:
                continue
            if line[0] == 'inet6' and not ipv6:
                continue

            addr = line[1]
            if '/' in addr:
                addr = addr.split('/')[0]
            addrs.append(addr)

        logging.debug('%s: get_ether_addrs: %r', self, addrs)
        return addrs

    def get_ether_mac(self):
        output = self.bash(f'ip addr list dev {self.ETH_DEV}')
        for line in output:
            # link/ether 02:42:ac:11:00:02 brd ff:ff:ff:ff:ff:ff link-netnsid 0
            line = line.strip().split()
            if line and line[0] == 'link/ether':
                return line[1]

        assert False, output

    def add_ipmaddr_ether(self, ip: str):
        cmd = f'python3 /app/third_party/openthread/repo/tests/scripts/thread-cert/mcast6.py {self.ETH_DEV} {ip} &'
        self.bash(cmd)

    def ping_ether(self, ipaddr, num_responses=1, size=None, timeout=5, ttl=None, interface='eth0') -> int:

        cmd = f'ping -6 {ipaddr} -I {interface} -c {num_responses} -W {timeout}'
        if size is not None:
            cmd += f' -s {size}'

        if ttl is not None:
            cmd += f' -t {ttl}'

        resp_count = 0

        try:
            for line in self.bash(cmd):
                if self.PING_RESPONSE_PATTERN.match(line):
                    resp_count += 1
        except subprocess.CalledProcessError:
            pass

        return resp_count

    def get_ip6_address(self, address_type: config.ADDRESS_TYPE):
        """Get specific type of IPv6 address configured on thread device.

        Args:
            address_type: the config.ADDRESS_TYPE type of IPv6 address.

        Returns:
            IPv6 address string.
        """
        if address_type == config.ADDRESS_TYPE.BACKBONE_GUA:
            return self._getBackboneGua()
        elif address_type == config.ADDRESS_TYPE.BACKBONE_LINK_LOCAL:
            return self._getInfraLinkLocalAddress()
        elif address_type == config.ADDRESS_TYPE.ONLINK_ULA:
            return self._getInfraUla()
        elif address_type == config.ADDRESS_TYPE.ONLINK_GUA:
            return self._getInfraGua()
        else:
            raise ValueError(f'unsupported address type: {address_type}')

    def _getBackboneGua(self) -> Optional[str]:
        for addr in self.get_ether_addrs():
            if re.match(config.BACKBONE_PREFIX_REGEX_PATTERN, addr, re.I):
                return addr

        return None

    def _getInfraUla(self) -> Optional[str]:
        """ Returns the ULA addresses autoconfigured on the infra link.
        """
        addrs = []
        for addr in self.get_ether_addrs():
            if re.match(config.ONLINK_PREFIX_REGEX_PATTERN, addr, re.I):
                addrs.append(addr)

        return addrs

    def _getInfraGua(self) -> Optional[str]:
        """ Returns the GUA addresses autoconfigured on the infra link.
        """

        gua_prefix = config.ONLINK_GUA_PREFIX.split('::/')[0]
        return [addr for addr in self.get_ether_addrs() if addr.startswith(gua_prefix)]

    def _getInfraLinkLocalAddress(self) -> Optional[str]:
        """ Returns the link-local address autoconfigured on the infra link, which is started with "fe80".
        """
        for addr in self.get_ether_addrs():
            if re.match(config.LINK_LOCAL_REGEX_PATTERN, addr, re.I):
                return addr

        return None

    def ping(self, *args, **kwargs):
        backbone = kwargs.pop('backbone', False)
        if backbone:
            return self.ping_ether(*args, **kwargs)
        else:
            return super().ping(*args, **kwargs)

    def udp_send_host(self, ipaddr, port, data, hop_limit=None):
        if hop_limit is None:
            if ipaddress.ip_address(ipaddr).is_multicast:
                hop_limit = 10
            else:
                hop_limit = 64
        cmd = f'python3 /app/third_party/openthread/repo/tests/scripts/thread-cert/udp_send_host.py {ipaddr} {port} "{data}" {hop_limit}'
        self.bash(cmd)

    def add_ipmaddr(self, *args, **kwargs):
        backbone = kwargs.pop('backbone', False)
        if backbone:
            return self.add_ipmaddr_ether(*args, **kwargs)
        else:
            return super().add_ipmaddr(*args, **kwargs)

    def ip_neighbors_flush(self):
        # clear neigh cache on linux
        self.bash(f'ip -6 neigh list dev {self.ETH_DEV}')
        self.bash(f'ip -6 neigh flush nud all nud failed nud noarp dev {self.ETH_DEV}')
        self.bash('ip -6 neigh list nud all dev %s | cut -d " " -f1 | sudo xargs -I{} ip -6 neigh delete {} dev %s' %
                  (self.ETH_DEV, self.ETH_DEV))
        self.bash(f'ip -6 neigh list dev {self.ETH_DEV}')

    def publish_mdns_service(self, instance_name, service_type, port, host_name, txt):
        """Publish an mDNS service on the Ethernet.

        :param instance_name: the service instance name.
        :param service_type: the service type in format of '<service_type>.<protocol>'.
        :param port: the port the service is at.
        :param host_name: the host name this service points to. The domain
                          should not be included.
        :param txt: a dictionary containing the key-value pairs of the TXT record.
        """
        txt_string = ' '.join([f'{key}={value}' for key, value in txt.items()])
        self.bash(f'avahi-publish -s {instance_name}  {service_type} {port} -H {host_name}.local {txt_string} &')

    def publish_mdns_host(self, hostname, addresses):
        """Publish an mDNS host on the Ethernet

        :param host_name: the host name this service points to. The domain
                          should not be included.
        :param addresses: a list of strings representing the addresses to
                          be registered with the host.
        """
        for address in addresses:
            self.bash(f'avahi-publish -a {hostname}.local {address} &')

    def browse_mdns_services(self, name, timeout=2):
        """ Browse mDNS services on the ethernet.

        :param name: the service type name in format of '<service-name>.<protocol>'.
        :param timeout: timeout value in seconds before returning.
        :return: A list of service instance names.
        """

        self.bash(f'dns-sd -Z {name} local. > /tmp/{name} 2>&1 &')
        time.sleep(timeout)
        self.bash('pkill dns-sd')

        instances = []
        for line in self.bash(f'cat /tmp/{name}', encoding='raw_unicode_escape'):
            elements = line.split()
            if len(elements) >= 3 and elements[0] == name and elements[1] == 'PTR':
                instances.append(elements[2][:-len('.' + name)])
        return instances

    def discover_mdns_service(self, instance, name, host_name, timeout=2):
        """ Discover/resolve the mDNS service on ethernet.

        :param instance: the service instance name.
        :param name: the service name in format of '<service-name>.<protocol>'.
        :param host_name: the host name this service points to. The domain
                          should not be included.
        :param timeout: timeout value in seconds before returning.
        :return: a dict of service properties or None.

        The return value is a dict with the same key/values of srp_server_get_service
        except that we don't have a `deleted` field here.
        """
        host_name_file = self.bash('mktemp')[0].strip()
        service_data_file = self.bash('mktemp')[0].strip()

        self.bash(f'dns-sd -Z {name} local. > {service_data_file} 2>&1 &')
        time.sleep(timeout)

        full_service_name = f'{instance}.{name}'
        # When hostname is unspecified, extract hostname from browse result
        if host_name is None:
            for line in self.bash(f'cat {service_data_file}', encoding='raw_unicode_escape'):
                elements = line.split()
                if len(elements) >= 6 and elements[0] == full_service_name and elements[1] == 'SRV':
                    host_name = elements[5].split('.')[0]
                    break

        assert (host_name is not None)
        self.bash(f'dns-sd -G v6 {host_name}.local. > {host_name_file} 2>&1 &')
        time.sleep(timeout)

        self.bash('pkill dns-sd')
        addresses = []
        service = {}

        logging.debug(self.bash(f'cat {host_name_file}', encoding='raw_unicode_escape'))
        logging.debug(self.bash(f'cat {service_data_file}', encoding='raw_unicode_escape'))

        # example output in the host file:
        # Timestamp     A/R Flags if Hostname                               Address                                     TTL
        # 9:38:09.274  Add     23 48 my-host.local.                         2001:0000:0000:0000:0000:0000:0000:0002%<0>  120
        #
        for line in self.bash(f'cat {host_name_file}', encoding='raw_unicode_escape'):
            elements = line.split()
            fullname = f'{host_name}.local.'
            if 'No Such Record' in line:
                continue
            if fullname not in elements:
                continue
            if 'Add' not in elements:
                continue
            addresses.append(elements[elements.index(fullname) + 1].split('%')[0])

        logging.debug(f'addresses of {host_name}: {addresses}')

        # example output of in the service file:
        # _ipps._tcp                                      PTR     my-service._ipps._tcp
        # my-service._ipps._tcp                           SRV     0 0 12345 my-host.local. ; Replace with unicast FQDN of target host
        # my-service._ipps._tcp                           TXT     ""
        #
        is_txt = False
        txt = ''
        for line in self.bash(f'cat {service_data_file}', encoding='raw_unicode_escape'):
            elements = line.split()
            if len(elements) >= 2 and elements[0] == full_service_name and elements[1] == 'TXT':
                is_txt = True
            if is_txt:
                txt += line.strip()
                if line.strip().endswith('"'):
                    is_txt = False
                    txt_dict = self.__parse_dns_sd_txt(txt)
                    logging.info(f'txt = {txt_dict}')
                    service['txt'] = txt_dict

            if not elements or elements[0] != full_service_name:
                continue
            if elements[1] == 'SRV':
                service['fullname'] = elements[0]
                service['instance'] = instance
                service['name'] = name
                service['priority'] = int(elements[2])
                service['weight'] = int(elements[3])
                service['port'] = int(elements[4])
                service['host_fullname'] = elements[5]
                assert (service['host_fullname'] == f'{host_name}.local.')
                service['host'] = host_name
                service['addresses'] = addresses
        return service or None

    def _start_radvd_and_verify(self):
        self.bash('service radvd start')

        output = self.bash('service radvd status')
        for line in output:
            if "running" in line:
                return
        raise Exception("Failed to start radvd service")

    def start_radvd_service(self, prefix, slaac):
        self.bash("""cat >/etc/radvd.conf <<EOF
interface eth0
{
    AdvSendAdvert on;

    AdvReachableTime 200;
    AdvRetransTimer 200;
    AdvDefaultLifetime 1800;
    MinRtrAdvInterval 1200;
    MaxRtrAdvInterval 1800;
    AdvDefaultPreference low;

    prefix %s
    {
        AdvOnLink on;
        AdvAutonomous %s;
        AdvRouterAddr off;
        AdvPreferredLifetime 1800;
        AdvValidLifetime 1800;
    };
};
EOF
""" % (prefix, 'on' if slaac else 'off'))
        self._start_radvd_and_verify()

    def start_pd_radvd_service(self, prefix):
        self.bash("""cat >/etc/radvd.conf <<EOF
interface wpan0
{
    AdvSendAdvert on;

    AdvReachableTime 20;
    AdvRetransTimer 20;
    AdvDefaultLifetime 180;
    MinRtrAdvInterval 120;
    MaxRtrAdvInterval 180;
    AdvDefaultPreference low;

    prefix %s
    {
        AdvOnLink on;
        AdvAutonomous on;
        AdvRouterAddr off;
        AdvPreferredLifetime 180;
        AdvValidLifetime 180;
    };
};
EOF
""" % (prefix,))
        self._start_radvd_and_verify()

    def start_rdnss_radvd_service(self, dns_server_address):
        self.bash(f"""cat >/etc/radvd.conf <<EOF
interface eth0
{{
    AdvSendAdvert on;

    AdvReachableTime 20;
    AdvRetransTimer 20;
    AdvDefaultLifetime 180;
    MinRtrAdvInterval 120;
    MaxRtrAdvInterval 180;
    AdvDefaultPreference low;

    RDNSS {dns_server_address}
    {{
        AdvRDNSSLifetime 1800;
    }};
}};
EOF
""")
        self._start_radvd_and_verify()

    def stop_radvd_service(self):
        self.bash('service radvd stop')

    def kill_radvd_service(self):
        self.bash('pkill radvd')

    def __parse_dns_sd_txt(self, line: str):
        # Example TXT entry:
        # "xp=\\000\\013\\184\\000\\000\\000\\000\\000"
        txt = {}
        for entry in re.findall(r'"((?:[^\\]|\\.)*?)"', line):
            if '=' not in entry:
                continue

            k, v = entry.split('=', 1)
            txt[k] = v

        return txt


class OtbrNode(LinuxHost, NodeImpl, OtbrDocker):
    TUN_DEV = config.THREAD_IFNAME
    is_otbr = True
    is_bbr = True  # OTBR is also BBR
    node_type = 'otbr-docker'

    def __repr__(self):
        return f'Otbr<{self.nodeid}>'

    def start(self):
        self._setup_sysctl()
        self.set_log_level(5)
        super().start()

    def add_ipaddr(self, addr):
        cmd = f'ip -6 addr add {addr}/64 dev {self.TUN_DEV}'
        self.bash(cmd)

    def add_ipmaddr_tun(self, ip: str):
        cmd = f'python3 /app/third_party/openthread/repo/tests/scripts/thread-cert/mcast6.py {self.TUN_DEV} {ip} &'
        self.bash(cmd)

    def get_ip6_address(self, address_type: config.ADDRESS_TYPE):
        try:
            return super(OtbrNode, self).get_ip6_address(address_type)
        except Exception as e:
            return super(LinuxHost, self).get_ip6_address(address_type)


class HostNode(LinuxHost, OtbrDocker):
    is_host = True

    def __init__(self, nodeid, name=None, **kwargs):
        self.nodeid = nodeid
        self.name = name or ('Host%d' % nodeid)
        super().__init__(nodeid, **kwargs)
        self.bash('service otbr-agent stop')

    def start(self, start_radvd=True, prefix=config.DOMAIN_PREFIX, slaac=False):
        self._setup_sysctl()
        if start_radvd:
            self.start_radvd_service(prefix, slaac)
        else:
            self.stop_radvd_service()

    def stop(self):
        self.stop_radvd_service()

    def get_addrs(self) -> List[str]:
        return self.get_ether_addrs()

    def __repr__(self):
        return f'Host<{self.nodeid}>'

    def get_matched_ula_addresses(self, prefix):
        """Get the IPv6 addresses that matches given prefix.
        """

        addrs = []
        for addr in self.get_ip6_address(config.ADDRESS_TYPE.ONLINK_ULA):
            if IPv6Address(addr) in IPv6Network(prefix):
                addrs.append(addr)

        return addrs


if __name__ == '__main__':
    unittest.main()
