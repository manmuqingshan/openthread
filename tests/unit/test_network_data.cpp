/*
 *  Copyright (c) 2017, The OpenThread Authors.
 *  All rights reserved.
 *
 *  Redistribution and use in source and binary forms, with or without
 *  modification, are permitted provided that the following conditions are met:
 *  1. Redistributions of source code must retain the above copyright
 *     notice, this list of conditions and the following disclaimer.
 *  2. Redistributions in binary form must reproduce the above copyright
 *     notice, this list of conditions and the following disclaimer in the
 *     documentation and/or other materials provided with the distribution.
 *  3. Neither the name of the copyright holder nor the
 *     names of its contributors may be used to endorse or promote products
 *     derived from this software without specific prior written permission.
 *
 *  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 *  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 *  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 *  ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
 *  LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 *  CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 *  SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 *  INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 *  CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 *  ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 *  POSSIBILITY OF SUCH DAMAGE.
 */

#include <openthread/config.h>

#include "common/array.hpp"
#include "common/code_utils.hpp"
#include "instance/instance.hpp"
#include "thread/network_data_leader.hpp"
#include "thread/network_data_local.hpp"
#include "thread/network_data_service.hpp"

#include "test_platform.h"
#include "test_util.hpp"

namespace ot {
namespace NetworkData {

void PrintExternalRouteConfig(const ExternalRouteConfig &aConfig)
{
    printf("\nroute-prefix:");

    for (uint8_t b : aConfig.mPrefix.mPrefix.mFields.m8)
    {
        printf("%02x", b);
    }

    printf(", length:%d, rloc16:%04x, preference:%d, nat64:%d, stable:%d, nexthop:%d", aConfig.mPrefix.mLength,
           aConfig.mRloc16, aConfig.mPreference, aConfig.mNat64, aConfig.mStable, aConfig.mNextHopIsThisDevice);
}

void PrintOnMeshPrefixConfig(const OnMeshPrefixConfig &aConfig)
{
    printf("\non-mesh-prefix:");

    for (uint8_t b : aConfig.mPrefix.mPrefix.mFields.m8)
    {
        printf("%02x", b);
    }

    printf(", length:%d, rloc16:%04x, preference:%d, stable:%d, def-route:%d", aConfig.mPrefix.mLength, aConfig.mRloc16,
           aConfig.mPreference, aConfig.mStable, aConfig.mDefaultRoute);
}

// Returns true if the two given ExternalRouteConfig match (intentionally ignoring mNextHopIsThisDevice).
bool CompareExternalRouteConfig(const otExternalRouteConfig &aConfig1, const otExternalRouteConfig &aConfig2)
{
    return (memcmp(aConfig1.mPrefix.mPrefix.mFields.m8, aConfig2.mPrefix.mPrefix.mFields.m8,
                   sizeof(aConfig1.mPrefix.mPrefix)) == 0) &&
           (aConfig1.mPrefix.mLength == aConfig2.mPrefix.mLength) && (aConfig1.mRloc16 == aConfig2.mRloc16) &&
           (aConfig1.mPreference == aConfig2.mPreference) && (aConfig1.mStable == aConfig2.mStable);
}

// Returns true if the two given OnMeshprefix match.
bool CompareOnMeshPrefixConfig(const otBorderRouterConfig &aConfig1, const otBorderRouterConfig &aConfig2)
{
    return (memcmp(aConfig1.mPrefix.mPrefix.mFields.m8, aConfig2.mPrefix.mPrefix.mFields.m8,
                   sizeof(aConfig1.mPrefix.mPrefix)) == 0) &&
           (aConfig1.mPrefix.mLength == aConfig2.mPrefix.mLength) && (aConfig1.mRloc16 == aConfig2.mRloc16) &&
           (aConfig1.mPreference == aConfig2.mPreference) && (aConfig1.mStable == aConfig2.mStable) &&
           (aConfig1.mDefaultRoute == aConfig2.mDefaultRoute) && (aConfig1.mOnMesh == aConfig2.mOnMesh);
}

template <uint8_t kLength> void VerifyRlocsArray(const Rlocs &aRlocs, const uint16_t (&aExpectedRlocs)[kLength])
{
    VerifyOrQuit(aRlocs.GetLength() == kLength);

    printf("\nRLOCs: { ");

    for (uint16_t rloc : aRlocs)
    {
        printf("0x%04x ", rloc);
    }

    printf("}");

    for (uint16_t index = 0; index < kLength; index++)
    {
        VerifyOrQuit(aRlocs.Contains(aExpectedRlocs[index]));
    }
}

void TestNetworkDataIterator(void)
{
    Instance           *instance;
    Iterator            iter = kIteratorInit;
    ExternalRouteConfig rconfig;
    OnMeshPrefixConfig  pconfig;
    Rlocs               rlocs;

    instance = testInitInstance();
    VerifyOrQuit(instance != nullptr);

    {
        // Network Data:
        // - An invalid TLV type.
        // - An invalid Prefix TLV with prefix length of 129 (and two HasRoute sub-TLVs).
        // - An invalid Prefix TLV with short length (length = 1)
        // - An invalid Prefix TLV with no prefix.
        // - A valid Prefix TLV with two HasRoute sub-TLVs

        const uint8_t kNetworkData[] = {
            0xff, 0x03, 0x01, 0x02, 0x03,

            0x03, 0x1D, 0x00, 0x81, 0xFD, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xAA, 0xBB,
            0xCC, 0xDD, 0xEE, 0xFF, 0x00, 0x00, 0x03, 0xb8, 0x00, 0x40, 0x01, 0x03, 0x14, 0x00, 0x00,

            0x03, 0x01, 0x00,

            0x03, 0x02, 0x00, 0x40,

            0x03, 0x14, 0x00, 0x40, 0xFD, 0x00, 0x12, 0x34, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0xC8, 0x00,
            0x40, 0x01, 0x03, 0x54, 0x00, 0x00,
        };

        otExternalRouteConfig routes[] = {
            {
                {
                    {{{0xfd, 0x00, 0x12, 0x34, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0xc800, // mRloc16
                1,      // mPreference
                false,  // mNat64
                false,  // mStable
                false,  // mNextHopIsThisDevice
                false,  // mAdvPio
            },
            {
                {
                    {{{0xfd, 0x00, 0x12, 0x34, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0x5400, // mRloc16
                0,      // mPreference
                false,  // mNat64
                true,   // mStable
                false,  // mNextHopIsThisDevice
                false,  // mAdvPio
            },
        };

        const uint16_t kRlocs[]            = {0xc800, 0x5400};
        const uint16_t kNonExistingRlocs[] = {0xc700, 0x0000, 0x5401};

        NetworkData netData(*instance, kNetworkData, sizeof(kNetworkData));

        iter = OT_NETWORK_DATA_ITERATOR_INIT;

        printf("\nTest #1: Network data 1");
        printf("\n-------------------------------------------------");

        for (const auto &route : routes)
        {
            SuccessOrQuit(netData.GetNextExternalRoute(iter, rconfig));
            PrintExternalRouteConfig(rconfig);
            VerifyOrQuit(CompareExternalRouteConfig(rconfig, route));
        }

        VerifyOrQuit(netData.GetNextExternalRoute(iter, rconfig) == kErrorNotFound);

        netData.FindRlocs(kAnyBrOrServer, kAnyRole, rlocs);
        VerifyRlocsArray(rlocs, kRlocs);

        netData.FindRlocs(kAnyBrOrServer, kRouterRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kRlocs);

        netData.FindRlocs(kAnyBrOrServer, kChildRoleOnly, rlocs);
        VerifyOrQuit(rlocs.GetLength() == 0);

        netData.FindRlocs(kBrProvidingExternalIpConn, kAnyRole, rlocs);
        VerifyRlocsArray(rlocs, kRlocs);
        VerifyOrQuit(netData.CountBorderRouters(kAnyRole) == GetArrayLength(kRlocs));

        netData.FindRlocs(kBrProvidingExternalIpConn, kRouterRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kRlocs);
        VerifyOrQuit(netData.CountBorderRouters(kRouterRoleOnly) == GetArrayLength(kRlocs));

        netData.FindRlocs(kBrProvidingExternalIpConn, kChildRoleOnly, rlocs);
        VerifyOrQuit(rlocs.GetLength() == 0);
        VerifyOrQuit(netData.CountBorderRouters(kChildRoleOnly) == 0);

        for (uint16_t rloc16 : kRlocs)
        {
            VerifyOrQuit(netData.ContainsBorderRouterWithRloc(rloc16));
        }

        for (uint16_t rloc16 : kNonExistingRlocs)
        {
            VerifyOrQuit(!netData.ContainsBorderRouterWithRloc(rloc16));
        }
    }

    {
        const uint8_t kNetworkData[] = {
            0x08, 0x04, 0x0B, 0x02, 0x00, 0x00, 0x03, 0x1E, 0x00, 0x40, 0xFD, 0x00, 0x12, 0x34, 0x56, 0x78, 0x00, 0x00,
            0x07, 0x02, 0x11, 0x40, 0x00, 0x03, 0x10, 0x00, 0x40, 0x01, 0x03, 0x54, 0x00, 0x00, 0x05, 0x04, 0x54, 0x00,
            0x31, 0x00, 0x02, 0x0F, 0x00, 0x40, 0xFD, 0x00, 0xAB, 0xBA, 0xCD, 0xDC, 0x00, 0x00, 0x00, 0x03, 0x10, 0x00,
            0x20, 0x03, 0x0E, 0x00, 0x20, 0xFD, 0x00, 0xAB, 0xBA, 0x01, 0x06, 0x54, 0x00, 0x00, 0x04, 0x01, 0x00,
        };

        otExternalRouteConfig routes[] = {
            {
                {
                    {{{0xfd, 0x00, 0x12, 0x34, 0x56, 0x78, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0x1000, // mRloc16
                1,      // mPreference
                false,  // mNat64
                false,  // mStable
                false,  // mNextHopIsThisDevice
            },
            {
                {
                    {{{0xfd, 0x00, 0x12, 0x34, 0x56, 0x78, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0x5400, // mRloc16
                0,      // mPreference
                false,  // mNat64
                true,   // mStable
                false,  // mNextHopIsThisDevice
            },
            {
                {
                    {{{0xfd, 0x00, 0xab, 0xba, 0xcd, 0xdc, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0x1000, // mRloc16
                0,      // mPreference
                true,   // mNat64
                false,  // mStable
                false,  // mNextHopIsThisDevice
            },
            {
                {
                    {{{0xfd, 0x00, 0xab, 0xba, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    32,
                },
                0x5400, // mRloc16
                0,      // mPreference
                false,  // mNat64
                true,   // mStable
                false,  // mNextHopIsThisDevice
            },
            {
                {
                    {{{0xfd, 0x00, 0xab, 0xba, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    32,
                },
                0x0401, // mRloc16
                0,      // mPreference
                false,  // mNat64
                true,   // mStable
                false,  // mNextHopIsThisDevice
            },
        };

        const uint16_t kRlocsAnyRole[]     = {0x1000, 0x5400, 0x0401};
        const uint16_t kRlocsRouterRole[]  = {0x1000, 0x5400};
        const uint16_t kRlocsChildRole[]   = {0x0401};
        const uint16_t kNonExistingRlocs[] = {0x6000, 0x0000, 0x0402};

        NetworkData netData(*instance, kNetworkData, sizeof(kNetworkData));

        iter = OT_NETWORK_DATA_ITERATOR_INIT;

        printf("\nTest #2: Network data 2");
        printf("\n-------------------------------------------------");

        for (const auto &route : routes)
        {
            SuccessOrQuit(netData.GetNextExternalRoute(iter, rconfig));
            PrintExternalRouteConfig(rconfig);
            VerifyOrQuit(CompareExternalRouteConfig(rconfig, route));
        }

        netData.FindRlocs(kAnyBrOrServer, kAnyRole, rlocs);
        VerifyRlocsArray(rlocs, kRlocsAnyRole);

        netData.FindRlocs(kAnyBrOrServer, kRouterRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kRlocsRouterRole);

        netData.FindRlocs(kAnyBrOrServer, kChildRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kRlocsChildRole);

        netData.FindRlocs(kBrProvidingExternalIpConn, kAnyRole, rlocs);
        VerifyRlocsArray(rlocs, kRlocsAnyRole);
        VerifyOrQuit(netData.CountBorderRouters(kAnyRole) == GetArrayLength(kRlocsAnyRole));

        netData.FindRlocs(kBrProvidingExternalIpConn, kRouterRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kRlocsRouterRole);
        VerifyOrQuit(netData.CountBorderRouters(kRouterRoleOnly) == GetArrayLength(kRlocsRouterRole));

        netData.FindRlocs(kBrProvidingExternalIpConn, kChildRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kRlocsChildRole);
        VerifyOrQuit(netData.CountBorderRouters(kChildRoleOnly) == GetArrayLength(kRlocsChildRole));

        netData.FindRlocs(kBrProvidingExternalIpConn, kAnyRole, rlocs);
        VerifyRlocsArray(rlocs, kRlocsAnyRole);

        for (uint16_t rloc16 : kRlocsAnyRole)
        {
            VerifyOrQuit(netData.ContainsBorderRouterWithRloc(rloc16));
        }

        for (uint16_t rloc16 : kNonExistingRlocs)
        {
            VerifyOrQuit(!netData.ContainsBorderRouterWithRloc(rloc16));
        }
    }

    {
        const uint8_t kNetworkData[] = {
            0x08, 0x04, 0x0b, 0x02, 0x36, 0xcc, 0x03, 0x1c, 0x00, 0x40, 0xfd, 0x00, 0xbe, 0xef, 0xca, 0xfe,
            0x00, 0x00, 0x05, 0x0c, 0x28, 0x00, 0x33, 0x00, 0x28, 0x01, 0x33, 0x00, 0x4c, 0x00, 0x31, 0x00,
            0x07, 0x02, 0x11, 0x40, 0x03, 0x14, 0x00, 0x40, 0xfd, 0x00, 0x22, 0x22, 0x00, 0x00, 0x00, 0x00,
            0x05, 0x04, 0x28, 0x00, 0x73, 0x00, 0x07, 0x02, 0x12, 0x40, 0x03, 0x12, 0x00, 0x40, 0xfd, 0x00,
            0x33, 0x33, 0x00, 0x00, 0x00, 0x00, 0x01, 0x06, 0xec, 0x00, 0x00, 0x28, 0x01, 0xc0,
        };

        otExternalRouteConfig routes[] = {
            {
                {
                    {{{0xfd, 0x00, 0x33, 0x33, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0xec00, // mRloc16
                0,      // mPreference
                false,  // mNat64
                true,   // mStable
                false,  // mNextHopIsThisDevice
            },
            {
                {
                    {{{0xfd, 0x00, 0x33, 0x33, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0x2801, // mRloc16
                -1,     // mPreference
                false,  // mNat64
                true,   // mStable
                false,  // mNextHopIsThisDevice
            },
        };

        otBorderRouterConfig prefixes[] = {
            {
                {
                    {{{0xfd, 0x00, 0xbe, 0xef, 0xca, 0xfe, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0,      // mPreference
                true,   // mPreferred
                true,   // mSlaac
                false,  // mDhcp
                true,   // mConfigure
                true,   // mDefaultRoute
                true,   // mOnMesh
                true,   // mStable
                false,  // mNdDns
                false,  // mDp
                0x2800, // mRloc16
            },
            {
                {
                    {{{0xfd, 0x00, 0xbe, 0xef, 0xca, 0xfe, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0,      // mPreference
                true,   // mPreferred
                true,   // mSlaac
                false,  // mDhcp
                true,   // mConfigure
                true,   // mDefaultRoute
                true,   // mOnMesh
                true,   // mStable
                false,  // mNdDns
                false,  // mDp
                0x2801, // mRloc16
            },
            {
                {
                    {{{0xfd, 0x00, 0xbe, 0xef, 0xca, 0xfe, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                0,      // mPreference
                true,   // mPreferred
                true,   // mSlaac
                false,  // mDhcp
                true,   // mConfigure
                false,  // mDefaultRoute
                true,   // mOnMesh
                true,   // mStable
                false,  // mNdDns
                false,  // mDp
                0x4c00, // mRloc16
            },
            {
                {
                    {{{0xfd, 0x00, 0x22, 0x22, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00}}},
                    64,
                },
                1,      // mPreference
                true,   // mPreferred
                true,   // mSlaac
                false,  // mDhcp
                true,   // mConfigure
                true,   // mDefaultRoute
                true,   // mOnMesh
                true,   // mStable
                false,  // mNdDns
                false,  // mDp
                0x2800, // mRloc16
            },
        };

        const uint16_t kRlocsAnyRole[]      = {0xec00, 0x2801, 0x2800, 0x4c00};
        const uint16_t kRlocsRouterRole[]   = {0xec00, 0x2800, 0x4c00};
        const uint16_t kRlocsChildRole[]    = {0x2801};
        const uint16_t kBrRlocsAnyRole[]    = {0xec00, 0x2801, 0x2800};
        const uint16_t kBrRlocsRouterRole[] = {0xec00, 0x2800};
        const uint16_t kBrRlocsChildRole[]  = {0x2801};
        const uint16_t kNonExistingRlocs[]  = {0x6000, 0x0000, 0x2806, 0x4c00};

        NetworkData netData(*instance, kNetworkData, sizeof(kNetworkData));

        printf("\nTest #3: Network data 3");
        printf("\n-------------------------------------------------");

        iter = OT_NETWORK_DATA_ITERATOR_INIT;

        for (const auto &route : routes)
        {
            SuccessOrQuit(netData.GetNextExternalRoute(iter, rconfig));
            PrintExternalRouteConfig(rconfig);
            VerifyOrQuit(CompareExternalRouteConfig(rconfig, route));
        }

        iter = OT_NETWORK_DATA_ITERATOR_INIT;

        for (const auto &prefix : prefixes)
        {
            SuccessOrQuit(netData.GetNextOnMeshPrefix(iter, pconfig));
            PrintOnMeshPrefixConfig(pconfig);
            VerifyOrQuit(CompareOnMeshPrefixConfig(pconfig, prefix));
        }

        netData.FindRlocs(kAnyBrOrServer, kAnyRole, rlocs);
        VerifyRlocsArray(rlocs, kRlocsAnyRole);

        netData.FindRlocs(kAnyBrOrServer, kRouterRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kRlocsRouterRole);

        netData.FindRlocs(kAnyBrOrServer, kChildRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kRlocsChildRole);

        netData.FindRlocs(kBrProvidingExternalIpConn, kAnyRole, rlocs);
        VerifyRlocsArray(rlocs, kBrRlocsAnyRole);
        VerifyOrQuit(netData.CountBorderRouters(kAnyRole) == GetArrayLength(kBrRlocsAnyRole));

        netData.FindRlocs(kBrProvidingExternalIpConn, kRouterRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kBrRlocsRouterRole);
        VerifyOrQuit(netData.CountBorderRouters(kRouterRoleOnly) == GetArrayLength(kBrRlocsRouterRole));

        netData.FindRlocs(kBrProvidingExternalIpConn, kChildRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kBrRlocsChildRole);
        VerifyOrQuit(netData.CountBorderRouters(kChildRoleOnly) == GetArrayLength(kBrRlocsChildRole));

        for (uint16_t rloc16 : kBrRlocsAnyRole)
        {
            VerifyOrQuit(netData.ContainsBorderRouterWithRloc(rloc16));
        }

        for (uint16_t rloc16 : kNonExistingRlocs)
        {
            VerifyOrQuit(!netData.ContainsBorderRouterWithRloc(rloc16));
        }
    }

    testFreeInstance(instance);
}

#if OPENTHREAD_CONFIG_TMF_NETDATA_SERVICE_ENABLE

class TestNetworkData : public Local
{
public:
    explicit TestNetworkData(Instance &aInstance)
        : Local(aInstance)
    {
    }

    Error AddService(const ServiceData &aServiceData)
    {
        return Local::AddService(ServiceTlv::kThreadEnterpriseNumber, aServiceData, true, ServerData());
    }

    Error ValidateServiceData(const ServiceTlv *aServiceTlv, const ServiceData &aServiceData) const
    {
        Error       error = kErrorFailed;
        ServiceData serviceData;

        VerifyOrExit(aServiceTlv != nullptr);
        aServiceTlv->GetServiceData(serviceData);

        VerifyOrExit(aServiceData == serviceData);
        error = kErrorNone;

    exit:
        return error;
    }

    void Test(void)
    {
        const uint8_t kServiceData1[] = {0x02};
        const uint8_t kServiceData2[] = {0xab};
        const uint8_t kServiceData3[] = {0xab, 0x00};
        const uint8_t kServiceData4[] = {0x02, 0xab, 0xcd, 0xef};
        const uint8_t kServiceData5[] = {0x02, 0xab, 0xcd};

        const ServiceTlv *tlv;
        ServiceData       serviceData1;
        ServiceData       serviceData2;
        ServiceData       serviceData3;
        ServiceData       serviceData4;
        ServiceData       serviceData5;

        serviceData1.InitFrom(kServiceData1);
        serviceData2.InitFrom(kServiceData2);
        serviceData3.InitFrom(kServiceData3);
        serviceData4.InitFrom(kServiceData4);
        serviceData5.InitFrom(kServiceData5);

        SuccessOrQuit(AddService(serviceData1));
        SuccessOrQuit(AddService(serviceData2));
        SuccessOrQuit(AddService(serviceData3));
        SuccessOrQuit(AddService(serviceData4));
        SuccessOrQuit(AddService(serviceData5));

        DumpBuffer("netdata", GetBytes(), GetLength());

        // Iterate through all entries that start with { 0x02 } (kServiceData1)
        tlv = nullptr;
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData1, kServicePrefixMatch);
        SuccessOrQuit(ValidateServiceData(tlv, serviceData1));
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData1, kServicePrefixMatch);
        SuccessOrQuit(ValidateServiceData(tlv, serviceData4));
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData1, kServicePrefixMatch);
        SuccessOrQuit(ValidateServiceData(tlv, serviceData5));
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData1, kServicePrefixMatch);
        VerifyOrQuit(tlv == nullptr, "FindNextService() returned extra TLV");

        // Iterate through all entries that start with { 0xab } (serviceData2)
        tlv = nullptr;
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData2, kServicePrefixMatch);
        SuccessOrQuit(ValidateServiceData(tlv, serviceData2));
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData2, kServicePrefixMatch);
        SuccessOrQuit(ValidateServiceData(tlv, serviceData3));
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData2, kServicePrefixMatch);
        VerifyOrQuit(tlv == nullptr, "FindNextService() returned extra TLV");

        // Iterate through all entries that start with serviceData5
        tlv = nullptr;
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData5, kServicePrefixMatch);
        SuccessOrQuit(ValidateServiceData(tlv, serviceData4));
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData5, kServicePrefixMatch);
        SuccessOrQuit(ValidateServiceData(tlv, serviceData5));
        tlv = FindNextService(tlv, ServiceTlv::kThreadEnterpriseNumber, serviceData5, kServicePrefixMatch);
        VerifyOrQuit(tlv == nullptr, "FindNextService() returned extra TLV");
    }
};

void TestNetworkDataFindNextService(void)
{
    Instance *instance;

    printf("\n\n-------------------------------------------------");
    printf("\nTestNetworkDataFindNextService()\n");

    instance = testInitInstance();
    VerifyOrQuit(instance != nullptr);

    {
        TestNetworkData netData(*instance);
        netData.Test();
    }
}

#endif // OPENTHREAD_CONFIG_TMF_NETDATA_SERVICE_ENABLE

void TestNetworkDataDsnSrpServices(void)
{
    class TestLeader : public Leader
    {
    public:
        void Populate(const uint8_t *aTlvs, uint8_t aTlvsLength)
        {
            memcpy(GetBytes(), aTlvs, aTlvsLength);
            SetLength(aTlvsLength);
        }
    };

    Instance *instance;

    printf("\n\n-------------------------------------------------");
    printf("\nTestNetworkDataDsnSrpServices()\n");

    instance = testInitInstance();
    VerifyOrQuit(instance != nullptr);

    {
        struct AnycastEntry
        {
            uint16_t mAloc16;
            uint8_t  mSequenceNumber;
            uint8_t  mVersion;
            uint16_t mRloc16;

            bool Matches(Service::DnsSrpAnycastInfo aInfo) const
            {
                VerifyOrQuit(aInfo.mAnycastAddress.GetIid().IsAnycastServiceLocator());

                return (aInfo.mAnycastAddress.GetIid().GetLocator() == mAloc16) &&
                       (aInfo.mSequenceNumber == mSequenceNumber) && (aInfo.mVersion == mVersion) &&
                       (aInfo.mRloc16 == mRloc16);
            }
        };

        struct UnicastEntry
        {
            const char *mAddress;
            uint16_t    mPort;
            uint8_t     mVersion;
            uint16_t    mRloc16;

            bool Matches(const Service::DnsSrpUnicastInfo &aInfo) const
            {
                Ip6::SockAddr sockAddr;

                SuccessOrQuit(sockAddr.GetAddress().FromString(mAddress));
                sockAddr.SetPort(mPort);

                return (aInfo.mSockAddr == sockAddr) && (aInfo.mVersion == mVersion) && (aInfo.mRloc16 == mRloc16);
            }
        };

        const uint8_t kNetworkData[] = {
            0x0b, 0x01, 0x00,

            0x0b, 0x0b, 0x80, 0x02, 0x5c, 0x02, 0x0d, 0x01, 0x00, 0x0d, 0x02, 0x28, 0x00,

            0x0b, 0x09, 0x81, 0x02, 0x5c, 0xff, 0x0d, 0x03, 0x6c, 0x00, 0x05,

            0x0b, 0x09, 0x82, 0x03, 0x5c, 0x03, 0xaa, 0x0d, 0x02, 0x4c, 0x00,

            0x0b, 0x36, 0x83, 0x14, 0x5d, 0xfd, 0xde, 0xad, 0x00, 0xbe, 0xef, 0x00, 0x00, 0x2d,
            0x0e, 0xc6, 0x27, 0x55, 0x56, 0x18, 0xd9, 0x12, 0x34, 0x03, 0x0d, 0x02, 0x00, 0x00,
            0x0d, 0x14, 0x6c, 0x00, 0xfd, 0x00, 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x00, 0x11,
            0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0xab, 0xcd, 0x0d, 0x04, 0x28, 0x00, 0x56, 0x78,

            0x0b, 0x24, 0x84, 0x01, 0x5d, 0x0d, 0x02, 0x00, 0x00, 0x0d, 0x15, 0x4c, 0x00, 0xfd,
            0x00, 0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc, 0xde, 0xf0, 0x01, 0x23, 0x45, 0x67, 0x89,
            0xab, 0x00, 0x0e, 0x01, 0x0d, 0x04, 0x6c, 0x00, 0xcd, 0x12,

            0x0b, 0x08, 0x84, 0x01, 0x5c, 0x0d, 0x02, 0x14, 0x01, 0x0d,

            0x0b, 0x07, 0x83, 0x01, 0x5c, 0x0d, 0x02, 0x28, 0x00,

            0x0b, 0x13, 0x83, 0x02, 0x5c, 0xfe, 0x0d, 0x03, 0x12, 0x00, 0x07, 0x0d, 0x03, 0x12,
            0x01, 0x06, 0x0d, 0x03, 0x16, 0x00, 0x07,
        };

        const AnycastEntry kAnycastEntries[] = {
            {0xfc10, 0x02, 0, 0x2800}, {0xfc11, 0xff, 5, 0x6c00}, {0xfc12, 0x03, 0, 0x4c00},
            {0xfc13, 0xfe, 7, 0x1200}, {0xfc13, 0xfe, 6, 0x1201}, {0xfc13, 0xfe, 7, 0x1600},
        };

        const UnicastEntry kUnicastEntriesFromServerData[] = {
            {"fd00:aabb:ccdd:eeff:11:2233:4455:6677", 0xabcd, 0, 0x6c00},
            {"fdde:ad00:beef:0:0:ff:fe00:2800", 0x5678, 0, 0x2800},
            {"fd00:1234:5678:9abc:def0:123:4567:89ab", 0x0e, 1, 0x4c00},
            {"fdde:ad00:beef:0:0:ff:fe00:6c00", 0xcd12, 0, 0x6c00},
        };

        const UnicastEntry kUnicastEntriesFromServiceData[] = {
            {"fdde:ad00:beef:0:2d0e:c627:5556:18d9", 0x1234, 3, 0x0000},
            {"fdde:ad00:beef:0:2d0e:c627:5556:18d9", 0x1234, 3, 0x6c00},
            {"fdde:ad00:beef:0:2d0e:c627:5556:18d9", 0x1234, 3, 0x2800},
        };

        const uint16_t kExpectedRlocs[]       = {0x6c00, 0x2800, 0x4c00, 0x0000, 0x1200, 0x1201, 0x1600, 0x1401};
        const uint16_t kExpectedRouterRlocs[] = {0x6c00, 0x2800, 0x4c00, 0x0000, 0x1200, 0x1600};
        const uint16_t kExpectedChildRlocs[]  = {0x1201, 0x1401};

        const uint8_t kPreferredAnycastEntryIndex = 2;

        Service::Manager          &manager = instance->Get<Service::Manager>();
        Service::Iterator          iterator(*instance);
        Service::DnsSrpAnycastInfo anycastInfo;
        Service::DnsSrpUnicastInfo unicastInfo;
        Service::DnsSrpUnicastType type;
        Rlocs                      rlocs;

        reinterpret_cast<TestLeader &>(instance->Get<Leader>()).Populate(kNetworkData, sizeof(kNetworkData));

        DumpBuffer("netdata", kNetworkData, sizeof(kNetworkData));

        // Verify `FindRlocs()`

        instance->Get<Leader>().FindRlocs(kAnyBrOrServer, kAnyRole, rlocs);
        VerifyRlocsArray(rlocs, kExpectedRlocs);

        instance->Get<Leader>().FindRlocs(kAnyBrOrServer, kRouterRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kExpectedRouterRlocs);

        instance->Get<Leader>().FindRlocs(kAnyBrOrServer, kChildRoleOnly, rlocs);
        VerifyRlocsArray(rlocs, kExpectedChildRlocs);

        instance->Get<Leader>().FindRlocs(kBrProvidingExternalIpConn, kAnyRole, rlocs);
        VerifyOrQuit(rlocs.GetLength() == 0);

        // Verify all the "DNS/SRP Anycast Service" entries in Network Data

        printf("\n- - - - - - - - - - - - - - - - - - - -");
        printf("\nDNS/SRP Anycast Service entries\n");

        for (const AnycastEntry &entry : kAnycastEntries)
        {
            SuccessOrQuit(iterator.GetNextDnsSrpAnycastInfo(anycastInfo));

            printf("\nanycastInfo { %s, seq:%d, rlco16:%04x, version:%u }",
                   anycastInfo.mAnycastAddress.ToString().AsCString(), anycastInfo.mSequenceNumber, anycastInfo.mRloc16,
                   anycastInfo.mVersion);

            VerifyOrQuit(entry.Matches(anycastInfo), "GetNextDnsSrpAnycastInfo() returned incorrect info");
        }

        VerifyOrQuit(iterator.GetNextDnsSrpAnycastInfo(anycastInfo) == kErrorNotFound,
                     "GetNextDnsSrpAnycastInfo() returned unexpected extra entry");

        // Find the preferred "DNS/SRP Anycast Service" entries in Network Data

        SuccessOrQuit(manager.FindPreferredDnsSrpAnycastInfo(anycastInfo));

        printf("\n\nPreferred anycastInfo { %s, seq:%d, version:%u }",
               anycastInfo.mAnycastAddress.ToString().AsCString(), anycastInfo.mSequenceNumber, anycastInfo.mVersion);

        VerifyOrQuit(kAnycastEntries[kPreferredAnycastEntryIndex].Matches(anycastInfo),
                     "FindPreferredDnsSrpAnycastInfo() returned invalid info");

        printf("\n\n- - - - - - - - - - - - - - - - - - - -");
        printf("\nDNS/SRP Unicast Service entries (server data)\n");

        iterator.Reset();
        type = Service::kAddrInServerData;

        for (const UnicastEntry &entry : kUnicastEntriesFromServerData)
        {
            SuccessOrQuit(iterator.GetNextDnsSrpUnicastInfo(type, unicastInfo));
            printf("\nunicastInfo { %s, rloc16:%04x }", unicastInfo.mSockAddr.ToString().AsCString(),
                   unicastInfo.mRloc16);

            VerifyOrQuit(entry.Matches(unicastInfo), "GetNextDnsSrpUnicastInfo() returned incorrect info");
        }

        VerifyOrQuit(iterator.GetNextDnsSrpUnicastInfo(type, unicastInfo) == kErrorNotFound,
                     "GetNextDnsSrpUnicastInfo() returned unexpected extra entry");

        printf("\n\n- - - - - - - - - - - - - - - - - - - -");
        printf("\nDNS/SRP Unicast Service entries (service data)\n");

        iterator.Reset();
        type = Service::kAddrInServiceData;

        for (const UnicastEntry &entry : kUnicastEntriesFromServiceData)
        {
            SuccessOrQuit(iterator.GetNextDnsSrpUnicastInfo(type, unicastInfo));
            printf("\nunicastInfo { %s, rloc16:%04x }", unicastInfo.mSockAddr.ToString().AsCString(),
                   unicastInfo.mRloc16);

            VerifyOrQuit(entry.Matches(unicastInfo), "GetNextDnsSrpUnicastInfo() returned incorrect info");
        }

        VerifyOrQuit(iterator.GetNextDnsSrpUnicastInfo(type, unicastInfo) == kErrorNotFound,
                     "GetNextDnsSrpUnicastInfo() returned unexpected extra entry");

        printf("\n");
    }

    testFreeInstance(instance);
}

void TestNetworkDataDsnSrpAnycastSeqNumSelection(void)
{
    class TestLeader : public Leader
    {
    public:
        void Populate(const uint8_t *aTlvs, uint8_t aTlvsLength)
        {
            memcpy(GetBytes(), aTlvs, aTlvsLength);
            SetLength(aTlvsLength);
        }
    };

    struct TestInfo
    {
        const uint8_t *mNetworkData;
        uint8_t        mNetworkDataLength;
        const uint8_t *mSeqNumbers;
        uint8_t        mSeqNumbersLength;
        uint8_t        mPreferredSeqNum;
        uint8_t        mPreferredVersion;
    };

    Instance *instance;

    printf("\n\n-------------------------------------------------");
    printf("\nTestNetworkDataDsnSrpAnycastSeqNumSelection()\n");

    instance = testInitInstance();
    VerifyOrQuit(instance != nullptr);

    const uint8_t kNetworkData1[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x01, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x81, 0x0d, 0x02, 0x50, 0x01, // Service TLV
    };
    const uint8_t kSeqNumbers1[]    = {1, 129};
    const uint8_t kPreferredSeqNum1 = 129;
    const uint8_t kPreferredVer1    = 0;

    const uint8_t kNetworkData2[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x85, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x05, 0x0d, 0x02, 0x50, 0x01, // Service TLV
    };
    const uint8_t kSeqNumbers2[]    = {133, 5};
    const uint8_t kPreferredSeqNum2 = 133;
    const uint8_t kPreferredVer2    = 0;

    const uint8_t kNetworkData3[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x01, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x02, 0x0d, 0x02, 0x50, 0x01, // Service TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0xff, 0x0d, 0x02, 0x50, 0x02, // Service TLV
    };
    const uint8_t kSeqNumbers3[]    = {1, 2, 255};
    const uint8_t kPreferredSeqNum3 = 2;
    const uint8_t kPreferredVer3    = 0;

    const uint8_t kNetworkData4[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x0a, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x82, 0x0d, 0x02, 0x50, 0x01, // Service TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0xfa, 0x0d, 0x02, 0x50, 0x02, // Service TLV
    };
    const uint8_t kSeqNumbers4[]    = {10, 130, 250};
    const uint8_t kPreferredSeqNum4 = 250;
    const uint8_t kPreferredVer4    = 0;

    const uint8_t kNetworkData5[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x82, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0xfa, 0x0d, 0x02, 0x50, 0x01, // Service TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0x0a, 0x0d, 0x02, 0x50, 0x02, // Service TLV
    };
    const uint8_t kSeqNumbers5[]    = {130, 250, 10};
    const uint8_t kPreferredSeqNum5 = 250;
    const uint8_t kPreferredVer5    = 0;

    const uint8_t kNetworkData6[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0xfa, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x0a, 0x0d, 0x02, 0x50, 0x01, // Service TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0x82, 0x0d, 0x02, 0x50, 0x02, // Service TLV
    };
    const uint8_t kSeqNumbers6[]    = {250, 10, 130};
    const uint8_t kPreferredSeqNum6 = 250;
    const uint8_t kPreferredVer6    = 0;

    const uint8_t kNetworkData7[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0xfa, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x0a, 0x0d, 0x02, 0x50, 0x01, // Service TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0x8A, 0x0d, 0x02, 0x50, 0x02, // Service TLV
    };
    const uint8_t kSeqNumbers7[]    = {250, 10, 138};
    const uint8_t kPreferredSeqNum7 = 250;
    const uint8_t kPreferredVer7    = 0;

    const uint8_t kNetworkData8[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x01, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x02, 0x0d, 0x02, 0x50, 0x01, // Service TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0xff, 0x0d, 0x02, 0x50, 0x02, // Service TLV
        0x0b, 0x08, 0x83, 0x02, 0x5c, 0xfe, 0x0d, 0x02, 0x50, 0x03, // Service TLV

    };
    const uint8_t kSeqNumbers8[]    = {1, 2, 255, 254};
    const uint8_t kPreferredSeqNum8 = 2;
    const uint8_t kPreferredVer8    = 0;

    const uint8_t kNetworkData9[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x01, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x02, 0x0d, 0x02, 0x50, 0x01, // Service TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0xff, 0x0d, 0x02, 0x50, 0x02, // Service TLV
        0x0b, 0x08, 0x83, 0x02, 0x5c, 0xfe, 0x0d, 0x02, 0x50, 0x03, // Service TLV

    };
    const uint8_t kSeqNumbers9[]    = {1, 2, 255, 254};
    const uint8_t kPreferredSeqNum9 = 2;
    const uint8_t kPreferredVer9    = 0;

    const uint8_t kNetworkData10[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0xfe, 0x0d, 0x02, 0x50, 0x00, // Server sub-TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x02, 0x0d, 0x02, 0x50, 0x01, // Server sub-TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0x78, 0x0d, 0x02, 0x50, 0x02, // Server sub-TLV
        0x0b, 0x08, 0x83, 0x02, 0x5c, 0x01, 0x0d, 0x02, 0x50, 0x03, // Server sub-TLV

    };
    const uint8_t kSeqNumbers10[]    = {254, 2, 120, 1};
    const uint8_t kPreferredSeqNum10 = 120;
    const uint8_t kPreferredVer10    = 0;

    const uint8_t kNetworkData11[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0xf0, 0x0d, 0x02, 0x50, 0x00, // Server sub-TLV
        0x0b, 0x08, 0x81, 0x02, 0x5c, 0x02, 0x0d, 0x02, 0x50, 0x01, // Server sub-TLV
        0x0b, 0x08, 0x82, 0x02, 0x5c, 0x78, 0x0d, 0x02, 0x50, 0x02, // Server sub-TLV
        0x0b, 0x08, 0x83, 0x02, 0x5c, 0x01, 0x0d, 0x02, 0x50, 0x03, // Server sub-TLV

    };
    const uint8_t kSeqNumbers11[]    = {240, 2, 120, 1};
    const uint8_t kPreferredSeqNum11 = 240;
    const uint8_t kPreferredVer11    = 0;

    const uint8_t kNetworkData12[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                               // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x01, 0x0d, 0x02, 0x50, 0x00,       // Service TLV
        0x0b, 0x09, 0x81, 0x02, 0x5c, 0x81, 0x0d, 0x03, 0x50, 0x01, 0x01, // Service TLV
    };
    const uint8_t kSeqNumbers12[]    = {1, 129};
    const uint8_t kPreferredSeqNum12 = 129;
    const uint8_t kPreferredVer12    = 1;

    const uint8_t kNetworkData13[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0,                         // Commissioning Data TLV
        0x0b, 0x08, 0x80, 0x02, 0x5c, 0x01, 0x0d, 0x02, 0x50, 0x00, // Service TLV
        0x0b, 0x0e, 0x81, 0x02, 0x5c, 0x81,                         // Service TLV
        0x0d, 0x03, 0x50, 0x01, 0x02,                               // Server sub-TLV
        0x0d, 0x03, 0x50, 0x02, 0x02,                               // Server sub-TLV
    };
    const uint8_t kSeqNumbers13[]    = {1, 129, 129};
    const uint8_t kPreferredSeqNum13 = 129;
    const uint8_t kPreferredVer13    = 2;

    const uint8_t kNetworkData14[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0, // Commissioning Data TLV
        0x0b, 0x13, 0x81, 0x02, 0x5c, 0x07, // Service TLV
        0x0d, 0x03, 0x50, 0x00, 0x01,       // Server sub-TLV
        0x0d, 0x03, 0x50, 0x01, 0x02,       // Server sub-TLV
        0x0d, 0x03, 0x50, 0x02, 0x03,       // Server sub-TLV
    };
    const uint8_t kSeqNumbers14[]    = {7, 7, 7};
    const uint8_t kPreferredSeqNum14 = 7;
    const uint8_t kPreferredVer14    = 1;

    const uint8_t kNetworkData15[] = {
        0x08, 0x04, 0x0b, 0x02, 0x50, 0xb0, // Commissioning Data TLV
        0x0b, 0x17, 0x81, 0x02, 0x5c, 0x03, // Service TLV
        0x0d, 0x03, 0x50, 0x00, 0x01,       // Server sub-TLV
        0x0d, 0x03, 0x50, 0x01, 0x02,       // Server sub-TLV
        0x0d, 0x02, 0x50, 0x02,             // Server sub-TLV
        0x0d, 0x03, 0x50, 0x03, 0x01,       // Server sub-TLV
    };
    const uint8_t kSeqNumbers15[]    = {3, 3, 3, 3};
    const uint8_t kPreferredSeqNum15 = 3;
    const uint8_t kPreferredVer15    = 0;

#define TEST_CASE(Num)                                                                            \
    {                                                                                             \
        kNetworkData##Num, sizeof(kNetworkData##Num), kSeqNumbers##Num, sizeof(kSeqNumbers##Num), \
            kPreferredSeqNum##Num, kPreferredVer##Num                                             \
    }

    const TestInfo kTests[] = {
        TEST_CASE(1),  TEST_CASE(2),  TEST_CASE(3),  TEST_CASE(4),  TEST_CASE(5),
        TEST_CASE(6),  TEST_CASE(7),  TEST_CASE(8),  TEST_CASE(9),  TEST_CASE(10),
        TEST_CASE(11), TEST_CASE(12), TEST_CASE(13), TEST_CASE(14), TEST_CASE(15),
    };

    Service::Manager &manager   = instance->Get<Service::Manager>();
    uint8_t           testIndex = 0;

    for (const TestInfo &test : kTests)
    {
        Service::Iterator          iterator(*instance);
        Service::DnsSrpAnycastInfo anycastInfo;

        reinterpret_cast<TestLeader &>(instance->Get<Leader>()).Populate(test.mNetworkData, test.mNetworkDataLength);

        printf("\n- - - - - - - - - - - - - - - - - - - -");
        printf("\nDNS/SRP Anycast Service entries for test %d", ++testIndex);

        for (uint8_t index = 0; index < test.mSeqNumbersLength; index++)
        {
            SuccessOrQuit(iterator.GetNextDnsSrpAnycastInfo(anycastInfo));

            printf("\n { %s, seq:%u, version:%u, rlco16:%04x }", anycastInfo.mAnycastAddress.ToString().AsCString(),

                   anycastInfo.mSequenceNumber, anycastInfo.mVersion, anycastInfo.mRloc16);

            VerifyOrQuit(anycastInfo.mSequenceNumber == test.mSeqNumbers[index]);
            VerifyOrQuit(anycastInfo.mRloc16 == 0x5000 + index);
        }

        VerifyOrQuit(iterator.GetNextDnsSrpAnycastInfo(anycastInfo) == kErrorNotFound);
        SuccessOrQuit(manager.FindPreferredDnsSrpAnycastInfo(anycastInfo));

        printf("\n preferred -> seq:%u, version:%u ", anycastInfo.mSequenceNumber, anycastInfo.mVersion);
        VerifyOrQuit(anycastInfo.mSequenceNumber == test.mPreferredSeqNum);
        VerifyOrQuit(anycastInfo.mVersion == test.mPreferredVersion);
    }

    testFreeInstance(instance);
}

} // namespace NetworkData
} // namespace ot

int main(void)
{
    ot::NetworkData::TestNetworkDataIterator();
#if OPENTHREAD_CONFIG_TMF_NETDATA_SERVICE_ENABLE
    ot::NetworkData::TestNetworkDataFindNextService();
#endif
    ot::NetworkData::TestNetworkDataDsnSrpServices();
    ot::NetworkData::TestNetworkDataDsnSrpAnycastSeqNumSelection();

    printf("\nAll tests passed\n");
    return 0;
}
