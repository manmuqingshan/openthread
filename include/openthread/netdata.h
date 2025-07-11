/*
 *  Copyright (c) 2016, The OpenThread Authors.
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

/**
 * @file
 * @brief
 *  This file defines the OpenThread Network Data API.
 */

#ifndef OPENTHREAD_NETDATA_H_
#define OPENTHREAD_NETDATA_H_

#include <stdbool.h>
#include <stdint.h>

#include <openthread/commissioner.h>
#include <openthread/error.h>
#include <openthread/instance.h>
#include <openthread/ip6.h>
#include <openthread/platform/radio.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @addtogroup api-thread-general
 *
 * @{
 */

#define OT_NETWORK_DATA_ITERATOR_INIT 0 ///< Value to initialize `otNetworkDataIterator`.

typedef uint32_t otNetworkDataIterator; ///< Used to iterate through Network Data information.

/**
 * Represents a Border Router configuration.
 */
typedef struct otBorderRouterConfig
{
    otIp6Prefix mPrefix;           ///< The IPv6 prefix.
    signed int  mPreference : 2;   ///< A 2-bit signed int preference (`OT_ROUTE_PREFERENCE_*` values).
    bool        mPreferred : 1;    ///< Whether prefix is preferred.
    bool        mSlaac : 1;        ///< Whether prefix can be used for address auto-configuration (SLAAC).
    bool        mDhcp : 1;         ///< Whether border router is DHCPv6 Agent.
    bool        mConfigure : 1;    ///< Whether DHCPv6 Agent supplying other config data.
    bool        mDefaultRoute : 1; ///< Whether border router is a default router for prefix.
    bool        mOnMesh : 1;       ///< Whether this prefix is considered on-mesh.
    bool        mStable : 1;       ///< Whether this configuration is considered Stable Network Data.
    bool        mNdDns : 1;        ///< Whether this border router can supply DNS information via ND.
    bool        mDp : 1;           ///< Whether prefix is a Thread Domain Prefix (added since Thread 1.2).
    uint16_t    mRloc16;           ///< The border router's RLOC16 (value ignored on config add).
} otBorderRouterConfig;

/**
 * Represents 6LoWPAN Context ID information associated with a prefix in Network Data.
 */
typedef struct otLowpanContextInfo
{
    uint8_t     mContextId;        ///< The 6LoWPAN Context ID.
    bool        mCompressFlag : 1; ///< The compress flag.
    bool        mStable : 1;       ///< Whether the Context TLV is marked as Stable Network Data.
    otIp6Prefix mPrefix;           ///< The associated IPv6 prefix.
} otLowpanContextInfo;

/**
 * Represents an External Route configuration.
 */
typedef struct otExternalRouteConfig
{
    otIp6Prefix mPrefix;                  ///< The IPv6 prefix.
    uint16_t    mRloc16;                  ///< The border router's RLOC16 (value ignored on config add).
    signed int  mPreference : 2;          ///< A 2-bit signed int preference (`OT_ROUTE_PREFERENCE_*` values).
    bool        mNat64 : 1;               ///< Whether this is a NAT64 prefix.
    bool        mStable : 1;              ///< Whether this configuration is considered Stable Network Data.
    bool        mNextHopIsThisDevice : 1; ///< Whether the next hop is this device (value ignored on config add).
    bool        mAdvPio : 1;              ///< Whether or not BR is advertising a ULA prefix in PIO (AP flag).
} otExternalRouteConfig;

/**
 * Defines valid values for `mPreference` in `otExternalRouteConfig` and `otBorderRouterConfig`.
 */
typedef enum otRoutePreference
{
    OT_ROUTE_PREFERENCE_LOW  = -1, ///< Low route preference.
    OT_ROUTE_PREFERENCE_MED  = 0,  ///< Medium route preference.
    OT_ROUTE_PREFERENCE_HIGH = 1,  ///< High route preference.
} otRoutePreference;

#define OT_SERVICE_DATA_MAX_SIZE 252 ///< Max size of Service Data in bytes.
#define OT_SERVER_DATA_MAX_SIZE 248  ///< Max size of Server Data in bytes. Theoretical limit, practically much lower.

/**
 * Represents a Server configuration.
 */
typedef struct otServerConfig
{
    bool     mStable : 1;                          ///< Whether this config is considered Stable Network Data.
    uint8_t  mServerDataLength;                    ///< Length of server data.
    uint8_t  mServerData[OT_SERVER_DATA_MAX_SIZE]; ///< Server data bytes.
    uint16_t mRloc16;                              ///< The Server RLOC16.
} otServerConfig;

/**
 * Represents a Service configuration.
 */
typedef struct otServiceConfig
{
    uint8_t        mServiceId;                             ///< Service ID (when iterating over the  Network Data).
    uint32_t       mEnterpriseNumber;                      ///< IANA Enterprise Number.
    uint8_t        mServiceDataLength;                     ///< Length of service data.
    uint8_t        mServiceData[OT_SERVICE_DATA_MAX_SIZE]; ///< Service data bytes.
    otServerConfig mServerConfig;                          ///< The Server configuration.
} otServiceConfig;

/**
 * Provide full or stable copy of the Partition's Thread Network Data.
 *
 * @param[in]      aInstance    A pointer to an OpenThread instance.
 * @param[in]      aStable      TRUE when copying the stable version, FALSE when copying the full version.
 * @param[out]     aData        A pointer to the data buffer.
 * @param[in,out]  aDataLength  On entry, size of the data buffer pointed to by @p aData.
 *                              On exit, number of copied bytes.
 *
 * @retval OT_ERROR_NONE    Successfully copied the Thread Network Data into @p aData and updated @p aDataLength.
 * @retval OT_ERROR_NO_BUFS Not enough space in @p aData to fully copy the Thread Network Data.
 */
otError otNetDataGet(otInstance *aInstance, bool aStable, uint8_t *aData, uint8_t *aDataLength);

/**
 * Get the current length (number of bytes) of Partition's Thread Network Data.
 *
 * @param[in] aInstance    A pointer to an OpenThread instance.
 *
 * @return The length of the Network Data.
 */
uint8_t otNetDataGetLength(otInstance *aInstance);

/**
 * Get the maximum observed length of the Thread Network Data since OT stack initialization or since the last call to
 * `otNetDataResetMaxLength()`.
 *
 * @param[in] aInstance    A pointer to an OpenThread instance.
 *
 * @return The maximum length of the Network Data (high water mark for Network Data length).
 */
uint8_t otNetDataGetMaxLength(otInstance *aInstance);

/**
 * Reset the tracked maximum length of the Thread Network Data.
 *
 * @param[in] aInstance    A pointer to an OpenThread instance.
 *
 * @sa otNetDataGetMaxLength
 */
void otNetDataResetMaxLength(otInstance *aInstance);

/**
 * Get the next On Mesh Prefix in the partition's Network Data.
 *
 * @param[in]      aInstance  A pointer to an OpenThread instance.
 * @param[in,out]  aIterator  A pointer to the Network Data iterator context. To get the first on-mesh entry
                              it should be set to OT_NETWORK_DATA_ITERATOR_INIT.
 * @param[out]     aConfig    A pointer to where the On Mesh Prefix information will be placed.
 *
 * @retval OT_ERROR_NONE       Successfully found the next On Mesh prefix.
 * @retval OT_ERROR_NOT_FOUND  No subsequent On Mesh prefix exists in the Thread Network Data.
 */
otError otNetDataGetNextOnMeshPrefix(otInstance            *aInstance,
                                     otNetworkDataIterator *aIterator,
                                     otBorderRouterConfig  *aConfig);

/**
 * Get the next external route in the partition's Network Data.
 *
 * @param[in]      aInstance  A pointer to an OpenThread instance.
 * @param[in,out]  aIterator  A pointer to the Network Data iterator context. To get the first external route entry
                              it should be set to OT_NETWORK_DATA_ITERATOR_INIT.
 * @param[out]     aConfig    A pointer to where the External Route information will be placed.
 *
 * @retval OT_ERROR_NONE       Successfully found the next External Route.
 * @retval OT_ERROR_NOT_FOUND  No subsequent external route entry exists in the Thread Network Data.
 */
otError otNetDataGetNextRoute(otInstance *aInstance, otNetworkDataIterator *aIterator, otExternalRouteConfig *aConfig);

/**
 * Get the next service in the partition's Network Data.
 *
 * @param[in]      aInstance  A pointer to an OpenThread instance.
 * @param[in,out]  aIterator  A pointer to the Network Data iterator context. To get the first service entry
                              it should be set to OT_NETWORK_DATA_ITERATOR_INIT.
 * @param[out]     aConfig    A pointer to where the service information will be placed.
 *
 * @retval OT_ERROR_NONE       Successfully found the next service.
 * @retval OT_ERROR_NOT_FOUND  No subsequent service exists in the partition's Network Data.
 */
otError otNetDataGetNextService(otInstance *aInstance, otNetworkDataIterator *aIterator, otServiceConfig *aConfig);

/**
 * Get the next 6LoWPAN Context ID info in the partition's Network Data.
 *
 * @param[in]      aInstance     A pointer to an OpenThread instance.
 * @param[in,out]  aIterator     A pointer to the Network Data iterator. To get the first service entry
                                 it should be set to OT_NETWORK_DATA_ITERATOR_INIT.
 * @param[out]     aContextInfo  A pointer to where the retrieved 6LoWPAN Context ID information will be placed.
 *
 * @retval OT_ERROR_NONE       Successfully found the next 6LoWPAN Context ID info.
 * @retval OT_ERROR_NOT_FOUND  No subsequent 6LoWPAN Context info exists in the partition's Network Data.
 */
otError otNetDataGetNextLowpanContextInfo(otInstance            *aInstance,
                                          otNetworkDataIterator *aIterator,
                                          otLowpanContextInfo   *aContextInfo);

/**
 * Gets the Commissioning Dataset from the partition's Network Data.
 *
 * @param[in]  aInstance   A pointer to the OpenThread instance.
 * @param[out] aDataset    A pointer to a `otCommissioningDataset` to populate.
 */
void otNetDataGetCommissioningDataset(otInstance *aInstance, otCommissioningDataset *aDataset);

/**
 * Get the Network Data Version.
 *
 * @param[in]  aInstance A pointer to an OpenThread instance.
 *
 * @returns The Network Data Version.
 */
uint8_t otNetDataGetVersion(otInstance *aInstance);

/**
 * Get the Stable Network Data Version.
 *
 * @param[in]  aInstance A pointer to an OpenThread instance.
 *
 * @returns The Stable Network Data Version.
 */
uint8_t otNetDataGetStableVersion(otInstance *aInstance);

/**
 * Check if the steering data includes a Joiner.
 *
 * @param[in]  aInstance          A pointer to an OpenThread instance.
 * @param[in]  aEui64             A pointer to the Joiner's IEEE EUI-64.
 *
 * @retval OT_ERROR_NONE          @p aEui64 is included in the steering data.
 * @retval OT_ERROR_INVALID_STATE No steering data present.
 * @retval OT_ERROR_NOT_FOUND     @p aEui64 is not included in the steering data.
 */
otError otNetDataSteeringDataCheckJoiner(otInstance *aInstance, const otExtAddress *aEui64);

// Forward declaration
struct otJoinerDiscerner;

/**
 * Check if the steering data includes a Joiner with a given discerner value.
 *
 * @param[in]  aInstance          A pointer to an OpenThread instance.
 * @param[in]  aDiscerner         A pointer to the Joiner Discerner.
 *
 * @retval OT_ERROR_NONE          @p aDiscerner is included in the steering data.
 * @retval OT_ERROR_INVALID_STATE No steering data present.
 * @retval OT_ERROR_NOT_FOUND     @p aDiscerner is not included in the steering data.
 */
otError otNetDataSteeringDataCheckJoinerWithDiscerner(otInstance                     *aInstance,
                                                      const struct otJoinerDiscerner *aDiscerner);

/**
 * Check whether a given Prefix can act as a valid OMR prefix and also the Leader's Network Data contains this prefix.
 *
 * @param[in]  aInstance  A pointer to an OpenThread instance.
 * @param[in]  aPrefix    A pointer to the IPv6 prefix.
 *
 * @returns  Whether @p aPrefix is a valid OMR prefix and Leader's Network Data contains the OMR prefix @p aPrefix.
 *
 * @note This API is only available when `OPENTHREAD_CONFIG_BORDER_ROUTING_ENABLE` is used.
 */
bool otNetDataContainsOmrPrefix(otInstance *aInstance, const otIp6Prefix *aPrefix);

/**
 * @}
 */

#ifdef __cplusplus
} // extern "C"
#endif

#endif // OPENTHREAD_NETDATA_H_
