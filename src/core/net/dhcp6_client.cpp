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
 *   This file implements DHCPv6 Client.
 */

#include "dhcp6_client.hpp"

#if OPENTHREAD_CONFIG_DHCP6_CLIENT_ENABLE

#include "instance/instance.hpp"

namespace ot {
namespace Dhcp6 {

RegisterLogModule("Dhcp6Client");

Client::Client(Instance &aInstance)
    : InstanceLocator(aInstance)
    , mSocket(aInstance, *this)
    , mTrickleTimer(aInstance, Client::HandleTrickleTimer)
    , mStartTime(0)
    , mIdentityAssociationCurrent(nullptr)
{
    ClearAllBytes(mIdentityAssociations);
}

void Client::HandleNotifierEvents(Events aEvents)
{
    if (aEvents.Contains(kEventThreadNetdataChanged))
    {
        UpdateAddresses();
    }
}

void Client::UpdateAddresses(void)
{
    bool                            found          = false;
    bool                            doesAgentExist = false;
    NetworkData::Iterator           iterator;
    NetworkData::OnMeshPrefixConfig config;

    // remove addresses directly if prefix not valid in network data
    for (IdentityAssociation &idAssociation : mIdentityAssociations)
    {
        if (idAssociation.mStatus == kIaStatusInvalid || idAssociation.mValidLifetime == 0)
        {
            continue;
        }

        found    = false;
        iterator = NetworkData::kIteratorInit;

        while (Get<NetworkData::Leader>().GetNextOnMeshPrefix(iterator, config) == kErrorNone)
        {
            if (!config.mDhcp)
            {
                continue;
            }

            if (idAssociation.mNetifAddress.HasPrefix(config.GetPrefix()))
            {
                found = true;
                break;
            }
        }

        if (!found)
        {
            Get<ThreadNetif>().RemoveUnicastAddress(idAssociation.mNetifAddress);
            idAssociation.mStatus = kIaStatusInvalid;
        }
    }

    // add IdentityAssociation for new configured prefix
    iterator = NetworkData::kIteratorInit;

    while (Get<NetworkData::Leader>().GetNextOnMeshPrefix(iterator, config) == kErrorNone)
    {
        IdentityAssociation *idAssociation = nullptr;

        if (!config.mDhcp)
        {
            continue;
        }

        doesAgentExist = true;
        found          = false;

        for (IdentityAssociation &ia : mIdentityAssociations)
        {
            if (ia.mStatus == kIaStatusInvalid)
            {
                // record an available IdentityAssociation
                if (idAssociation == nullptr)
                {
                    idAssociation = &ia;
                }
            }
            else if (ia.mNetifAddress.HasPrefix(config.GetPrefix()))
            {
                found         = true;
                idAssociation = &ia;
                break;
            }
        }

        if (!found)
        {
            if (idAssociation != nullptr)
            {
                idAssociation->mNetifAddress.mAddress      = config.mPrefix.mPrefix;
                idAssociation->mNetifAddress.mPrefixLength = config.mPrefix.mLength;
                idAssociation->mStatus                     = kIaStatusSolicit;
                idAssociation->mValidLifetime              = 0;
            }
            else
            {
                LogWarn("Insufficient memory for new DHCP prefix");
                continue;
            }
        }

        idAssociation->mPrefixAgentRloc = config.mRloc16;
    }

    if (doesAgentExist)
    {
        Start();
    }
    else
    {
        Stop();
    }
}

void Client::Start(void)
{
    VerifyOrExit(!mSocket.IsBound());

    IgnoreError(mSocket.Open(Ip6::kNetifThreadInternal));
    IgnoreError(mSocket.Bind(kDhcpClientPort));

    ProcessNextIdentityAssociation();

exit:
    return;
}

void Client::Stop(void)
{
    mTrickleTimer.Stop();
    IgnoreError(mSocket.Close());
}

bool Client::ProcessNextIdentityAssociation(void)
{
    bool rval = false;

    // not interrupt in-progress solicit
    VerifyOrExit(mIdentityAssociationCurrent == nullptr || mIdentityAssociationCurrent->mStatus != kIaStatusSoliciting);

    mTrickleTimer.Stop();

    for (IdentityAssociation &idAssociation : mIdentityAssociations)
    {
        if (idAssociation.mStatus != kIaStatusSolicit)
        {
            continue;
        }

        mTransactionId.GenerateRandom();

        mIdentityAssociationCurrent = &idAssociation;

        mTrickleTimer.Start(TrickleTimer::kModeTrickle, Time::SecToMsec(kTrickleTimerImin),
                            Time::SecToMsec(kTrickleTimerImax));

        mTrickleTimer.IndicateInconsistent();

        ExitNow(rval = true);
    }

exit:
    return rval;
}

void Client::HandleTrickleTimer(TrickleTimer &aTrickleTimer) { aTrickleTimer.Get<Client>().HandleTrickleTimer(); }

void Client::HandleTrickleTimer(void)
{
    OT_ASSERT(mSocket.IsBound());

    VerifyOrExit(mIdentityAssociationCurrent != nullptr, mTrickleTimer.Stop());

    switch (mIdentityAssociationCurrent->mStatus)
    {
    case kIaStatusSolicit:
        mStartTime                           = TimerMilli::GetNow();
        mIdentityAssociationCurrent->mStatus = kIaStatusSoliciting;

        OT_FALL_THROUGH;

    case kIaStatusSoliciting:
        Solicit(mIdentityAssociationCurrent->mPrefixAgentRloc);
        break;

    case kIaStatusSolicitReplied:
        mIdentityAssociationCurrent = nullptr;

        if (!ProcessNextIdentityAssociation())
        {
            Stop();
            mTrickleTimer.Stop();
        }

        break;

    default:
        break;
    }

exit:
    return;
}

void Client::Solicit(uint16_t aRloc16)
{
    Error            error = kErrorNone;
    Message         *message;
    Ip6::MessageInfo messageInfo;

    VerifyOrExit((message = mSocket.NewMessage()) != nullptr, error = kErrorNoBufs);

    SuccessOrExit(error = AppendHeader(*message));
    SuccessOrExit(error = AppendElapsedTimeOption(*message));
    SuccessOrExit(error = AppendClientIdOption(*message));
    SuccessOrExit(error = AppendIaNaOption(*message, aRloc16));
    // specify which prefixes to solicit
    SuccessOrExit(error = AppendIaAddressOption(*message, aRloc16));
    SuccessOrExit(error = AppendRapidCommitOption(*message));

#if OPENTHREAD_ENABLE_DHCP6_MULTICAST_SOLICIT
    messageInfo.GetPeerAddr().SetToRealmLocalAllRoutersMulticast();
#else
    messageInfo.GetPeerAddr().SetToRoutingLocator(Get<Mle::Mle>().GetMeshLocalPrefix(), aRloc16);
#endif
    messageInfo.SetSockAddr(Get<Mle::Mle>().GetMeshLocalRloc());
    messageInfo.mPeerPort = kDhcpServerPort;

    SuccessOrExit(error = mSocket.SendTo(*message, messageInfo));
    LogInfo("solicit");

exit:
    if (error != kErrorNone)
    {
        FreeMessage(message);
        LogWarnOnError(error, "send DHCPv6 Solicit");
    }
}

Error Client::AppendHeader(Message &aMessage)
{
    Header header;

    header.Clear();
    header.SetMsgType(kMsgTypeSolicit);
    header.SetTransactionId(mTransactionId);
    return aMessage.Append(header);
}

Error Client::AppendElapsedTimeOption(Message &aMessage)
{
    ElapsedTimeOption option;

    option.Init();
    option.SetElapsedTime(static_cast<uint16_t>(Time::MsecToSec(TimerMilli::GetNow() - mStartTime)));
    return aMessage.Append(option);
}

Error Client::AppendClientIdOption(Message &aMessage)
{
    ClientIdOption  option;
    Mac::ExtAddress eui64;

    Get<Radio>().GetIeeeEui64(eui64);

    option.Init();
    option.SetDuidType(kDuidLinkLayerAddress);
    option.SetDuidHardwareType(kHardwareTypeEui64);
    option.SetDuidLinkLayerAddress(eui64);

    return aMessage.Append(option);
}

Error Client::AppendIaNaOption(Message &aMessage, uint16_t aRloc16)
{
    Error      error  = kErrorNone;
    uint8_t    count  = 0;
    uint16_t   length = 0;
    IaNaOption option;

    VerifyOrExit(mIdentityAssociationCurrent != nullptr, error = kErrorDrop);

    for (IdentityAssociation &idAssociation : mIdentityAssociations)
    {
        if (idAssociation.mStatus == kIaStatusInvalid || idAssociation.mStatus == kIaStatusSolicitReplied)
        {
            continue;
        }

        if (idAssociation.mPrefixAgentRloc == aRloc16)
        {
            count++;
        }
    }

    // compute the right length
    length = sizeof(IaNaOption) + sizeof(IaAddressOption) * count - sizeof(Option);

    option.Init();
    option.SetLength(length);
    option.SetIaid(0);
    option.SetT1(0);
    option.SetT2(0);
    SuccessOrExit(error = aMessage.Append(option));

exit:
    return error;
}

Error Client::AppendIaAddressOption(Message &aMessage, uint16_t aRloc16)
{
    Error           error = kErrorNone;
    IaAddressOption option;

    VerifyOrExit(mIdentityAssociationCurrent, error = kErrorDrop);

    option.Init();

    for (IdentityAssociation &idAssociation : mIdentityAssociations)
    {
        if ((idAssociation.mStatus == kIaStatusSolicit || idAssociation.mStatus == kIaStatusSoliciting) &&
            (idAssociation.mPrefixAgentRloc == aRloc16))
        {
            option.SetAddress(idAssociation.mNetifAddress.GetAddress());
            option.SetPreferredLifetime(0);
            option.SetValidLifetime(0);
            SuccessOrExit(error = aMessage.Append(option));
        }
    }

exit:
    return error;
}

Error Client::AppendRapidCommitOption(Message &aMessage)
{
    RapidCommitOption option;

    option.Init();
    return aMessage.Append(option);
}

void Client::HandleUdpReceive(Message &aMessage, const Ip6::MessageInfo &aMessageInfo)
{
    OT_UNUSED_VARIABLE(aMessageInfo);

    Header header;

    SuccessOrExit(aMessage.Read(aMessage.GetOffset(), header));
    aMessage.MoveOffset(sizeof(header));

    if ((header.GetMsgType() == kMsgTypeReply) && (header.GetTransactionId() == mTransactionId))
    {
        ProcessReply(aMessage);
    }

exit:
    return;
}

void Client::ProcessReply(Message &aMessage)
{
    uint16_t offset = aMessage.GetOffset();
    uint16_t length = aMessage.GetLength() - aMessage.GetOffset();
    uint16_t optionOffset;

    if ((optionOffset = FindOption(aMessage, offset, length, Option::kStatusCode)) > 0)
    {
        SuccessOrExit(ProcessStatusCodeOption(aMessage, optionOffset));
    }

    // Server Identifier
    VerifyOrExit((optionOffset = FindOption(aMessage, offset, length, Option::kServerId)) > 0);
    SuccessOrExit(ProcessServerIdOption(aMessage, optionOffset));

    // Client Identifier
    VerifyOrExit((optionOffset = FindOption(aMessage, offset, length, Option::kClientId)) > 0);
    SuccessOrExit(ProcessClientIdOption(aMessage, optionOffset));

    // Rapid Commit
    VerifyOrExit(FindOption(aMessage, offset, length, Option::kRapidCommit) > 0);

    // IA_NA
    VerifyOrExit((optionOffset = FindOption(aMessage, offset, length, Option::kIaNa)) > 0);
    SuccessOrExit(ProcessIaNaOption(aMessage, optionOffset));

    HandleTrickleTimer();

exit:
    return;
}

uint16_t Client::FindOption(Message &aMessage, uint16_t aOffset, uint16_t aLength, Option::Code aCode)
{
    uint32_t offset = aOffset;
    uint16_t end    = aOffset + aLength;
    uint16_t rval   = 0;

    while (offset <= end)
    {
        Option option;

        SuccessOrExit(aMessage.Read(static_cast<uint16_t>(offset), option));

        if (option.GetCode() == aCode)
        {
            ExitNow(rval = static_cast<uint16_t>(offset));
        }

        offset += sizeof(option) + option.GetLength();
    }

exit:
    return rval;
}

Error Client::ProcessServerIdOption(Message &aMessage, uint16_t aOffset)
{
    Error          error = kErrorNone;
    ServerIdOption option;

    SuccessOrExit(aMessage.Read(aOffset, option));
    VerifyOrExit(((option.GetDuidType() == kDuidLinkLayerAddressPlusTime) &&
                  (option.GetDuidHardwareType() == kHardwareTypeEthernet)) ||
                     ((option.GetLength() == (sizeof(option) - sizeof(Option))) &&
                      (option.GetDuidType() == kDuidLinkLayerAddress) &&
                      (option.GetDuidHardwareType() == kHardwareTypeEui64)),
                 error = kErrorParse);
exit:
    return error;
}

Error Client::ProcessClientIdOption(Message &aMessage, uint16_t aOffset)
{
    Error           error = kErrorNone;
    ClientIdOption  option;
    Mac::ExtAddress eui64;

    Get<Radio>().GetIeeeEui64(eui64);

    SuccessOrExit(error = aMessage.Read(aOffset, option));
    VerifyOrExit(
        (option.GetLength() == (sizeof(option) - sizeof(Option))) && (option.GetDuidType() == kDuidLinkLayerAddress) &&
            (option.GetDuidHardwareType() == kHardwareTypeEui64) && (option.GetDuidLinkLayerAddress() == eui64),
        error = kErrorParse);
exit:
    return error;
}

Error Client::ProcessIaNaOption(Message &aMessage, uint16_t aOffset)
{
    Error      error = kErrorNone;
    IaNaOption option;
    uint16_t   optionOffset;
    uint16_t   length;

    SuccessOrExit(error = aMessage.Read(aOffset, option));

    aOffset += sizeof(option);
    length = option.GetLength() - (sizeof(option) - sizeof(Option));

    VerifyOrExit(length <= aMessage.GetLength() - aOffset, error = kErrorParse);

    if ((optionOffset = FindOption(aMessage, aOffset, length, Option::kStatusCode)) > 0)
    {
        SuccessOrExit(error = ProcessStatusCodeOption(aMessage, optionOffset));
    }

    while (length > 0)
    {
        if ((optionOffset = FindOption(aMessage, aOffset, length, Option::kIaAddress)) == 0)
        {
            ExitNow();
        }

        SuccessOrExit(error = ProcessIaAddressOption(aMessage, optionOffset));

        length -= ((optionOffset - aOffset) + sizeof(IaAddressOption));
        aOffset = optionOffset + sizeof(IaAddressOption);
    }

exit:
    return error;
}

Error Client::ProcessStatusCodeOption(Message &aMessage, uint16_t aOffset)
{
    Error            error = kErrorNone;
    StatusCodeOption option;

    SuccessOrExit(error = aMessage.Read(aOffset, option));
    VerifyOrExit((option.GetLength() >= sizeof(option) - sizeof(Option)) &&
                     (option.GetStatusCode() == StatusCodeOption::kSuccess),
                 error = kErrorParse);

exit:
    return error;
}

Error Client::ProcessIaAddressOption(Message &aMessage, uint16_t aOffset)
{
    Error           error;
    IaAddressOption option;

    SuccessOrExit(error = aMessage.Read(aOffset, option));
    VerifyOrExit(option.GetLength() == sizeof(option) - sizeof(Option), error = kErrorParse);

    for (IdentityAssociation &idAssociation : mIdentityAssociations)
    {
        if (idAssociation.mStatus == kIaStatusInvalid || idAssociation.mValidLifetime != 0)
        {
            continue;
        }

        if (idAssociation.mNetifAddress.GetAddress().PrefixMatch(option.GetAddress()) >=
            idAssociation.mNetifAddress.mPrefixLength)
        {
            idAssociation.mNetifAddress.mAddress       = option.GetAddress();
            idAssociation.mPreferredLifetime           = option.GetPreferredLifetime();
            idAssociation.mValidLifetime               = option.GetValidLifetime();
            idAssociation.mNetifAddress.mAddressOrigin = Ip6::Netif::kOriginDhcp6;
            idAssociation.mNetifAddress.mPreferred     = option.GetPreferredLifetime() != 0;
            idAssociation.mNetifAddress.mValid         = option.GetValidLifetime() != 0;
            idAssociation.mStatus                      = kIaStatusSolicitReplied;
            Get<ThreadNetif>().AddUnicastAddress(idAssociation.mNetifAddress);
            ExitNow(error = kErrorNone);
        }
    }

    error = kErrorNotFound;

exit:
    return error;
}

} // namespace Dhcp6
} // namespace ot

#endif // OPENTHREAD_CONFIG_DHCP6_CLIENT_ENABLE
