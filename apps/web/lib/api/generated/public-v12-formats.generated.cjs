// Generated from canonical Pydantic JSON Schemas. DO NOT EDIT.
"use strict";
const RFC3339_DATE_TIME_RE = /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[Zz]|([+-])(\d{2}):(\d{2}))$/;
const RFC3986_SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]*$/;
const RFC3986_SEGMENT_RE = /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=:@])*$/;
const RFC3986_SEGMENT_NZ_RE = /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=:@])+$/;
const RFC3986_QUERY_OR_FRAGMENT_RE = /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=:@]|[/?])*$/;
const RFC3986_USERINFO_RE = /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=:])*$/;
const RFC3986_REG_NAME_RE = /^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2}|[!$&'()*+,;=])*$/;
const RFC3986_PORT_RE = /^[0-9]*$/;
const RFC3986_H16_RE = /^[0-9A-Fa-f]{1,4}$/;
const RFC3986_IPVFUTURE_RE = /^[Vv][0-9A-Fa-f]+\.(?:[A-Za-z0-9._~-]|[!$&'()*+,;=]|:)+$/;

function isRfc3339LeapYear(year) {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function rfc3339DaysInMonth(year, month) {
  const days = [0, 31, isRfc3339LeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return days[month] ?? 0;
}

function validateRfc3339DateTime(value) {
  if (typeof value !== "string") return false;
  const match = RFC3339_DATE_TIME_RE.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = Number(match[8] ?? 0);
  const offsetMinute = Number(match[9] ?? 0);
  if (month < 1 || month > 12 || day < 1 || day > rfc3339DaysInMonth(year, month)) {
    return false;
  }
  if (hour > 23 || minute > 59 || offsetHour > 23 || offsetMinute > 59 || second > 60) {
    return false;
  }
  if (second < 60) return true;

  // RFC 3339 permits 60 only for a real UTC June/December leap-second position.
  const instant = new Date(0);
  instant.setUTCFullYear(year, month - 1, day);
  instant.setUTCHours(hour, minute, 59, 0);
  const offsetSign = match[7] === "-" ? -1 : 1;
  const offsetMinutes = match[7] ? offsetSign * (offsetHour * 60 + offsetMinute) : 0;
  instant.setTime(instant.getTime() - offsetMinutes * 60_000);
  return (
    instant.getUTCHours() === 23 &&
    instant.getUTCMinutes() === 59 &&
    (
      (instant.getUTCMonth() === 5 && instant.getUTCDate() === 30) ||
      (instant.getUTCMonth() === 11 && instant.getUTCDate() === 31)
    )
  );
}

function validateRfc3986Ipv4Address(value) {
  const octets = value.split(".");
  if (octets.length !== 4) return false;
  return octets.every((octet) => {
    if (!/^(?:0|[1-9][0-9]{0,2})$/.test(octet)) return false;
    return Number(octet) <= 255;
  });
}

function rfc3986Ipv6UnitCount(side, allowIpv4AtEnd) {
  if (side === "") return 0;
  const pieces = side.split(":");
  let units = 0;
  for (let index = 0; index < pieces.length; index += 1) {
    const piece = pieces[index];
    if (piece.includes(".")) {
      if (
        !allowIpv4AtEnd ||
        index !== pieces.length - 1 ||
        !validateRfc3986Ipv4Address(piece)
      ) {
        return -1;
      }
      units += 2;
    } else {
      if (!RFC3986_H16_RE.test(piece)) return -1;
      units += 1;
    }
  }
  return units;
}

function validateRfc3986Ipv6Address(value) {
  const compressionIndex = value.indexOf("::");
  if (compressionIndex === -1) {
    return rfc3986Ipv6UnitCount(value, true) === 8;
  }
  if (value.indexOf("::", compressionIndex + 2) !== -1) return false;

  const leftUnits = rfc3986Ipv6UnitCount(value.slice(0, compressionIndex), false);
  const rightUnits = rfc3986Ipv6UnitCount(value.slice(compressionIndex + 2), true);
  return leftUnits >= 0 && rightUnits >= 0 && leftUnits + rightUnits < 8;
}

function validateRfc3986Authority(value) {
  const atIndex = value.indexOf("@");
  let hostAndPort = value;
  if (atIndex !== -1) {
    if (atIndex !== value.lastIndexOf("@")) return false;
    if (!RFC3986_USERINFO_RE.test(value.slice(0, atIndex))) return false;
    hostAndPort = value.slice(atIndex + 1);
  }

  if (hostAndPort.startsWith("[")) {
    const closeIndex = hostAndPort.indexOf("]");
    if (closeIndex === -1 || hostAndPort.indexOf("]", closeIndex + 1) !== -1) {
      return false;
    }
    const ipLiteral = hostAndPort.slice(1, closeIndex);
    if (
      !validateRfc3986Ipv6Address(ipLiteral) &&
      !RFC3986_IPVFUTURE_RE.test(ipLiteral)
    ) {
      return false;
    }
    const portSuffix = hostAndPort.slice(closeIndex + 1);
    return portSuffix === "" || (
      portSuffix.startsWith(":") && RFC3986_PORT_RE.test(portSuffix.slice(1))
    );
  }

  if (hostAndPort.includes("[") || hostAndPort.includes("]")) return false;
  const colonIndex = hostAndPort.indexOf(":");
  if (colonIndex !== -1 && colonIndex !== hostAndPort.lastIndexOf(":")) return false;
  const host = colonIndex === -1 ? hostAndPort : hostAndPort.slice(0, colonIndex);
  const port = colonIndex === -1 ? "" : hostAndPort.slice(colonIndex + 1);
  return RFC3986_REG_NAME_RE.test(host) && RFC3986_PORT_RE.test(port);
}

function validateRfc3986HierPart(value) {
  if (value.startsWith("//")) {
    const pathIndex = value.indexOf("/", 2);
    const authority = pathIndex === -1 ? value.slice(2) : value.slice(2, pathIndex);
    const path = pathIndex === -1 ? "" : value.slice(pathIndex);
    if (!validateRfc3986Authority(authority)) return false;
    if (path === "") return true;
    return path.slice(1).split("/").every((segment) => RFC3986_SEGMENT_RE.test(segment));
  }

  if (value === "") return true;
  if (value.startsWith("/")) {
    if (value === "/") return true;
    const segments = value.slice(1).split("/");
    return (
      RFC3986_SEGMENT_NZ_RE.test(segments[0]) &&
      segments.slice(1).every((segment) => RFC3986_SEGMENT_RE.test(segment))
    );
  }

  const segments = value.split("/");
  return (
    RFC3986_SEGMENT_NZ_RE.test(segments[0]) &&
    segments.slice(1).every((segment) => RFC3986_SEGMENT_RE.test(segment))
  );
}

function validateAbsoluteRfc3986Uri(value) {
  if (typeof value !== "string") return false;
  const schemeEnd = value.indexOf(":");
  if (schemeEnd === -1 || !RFC3986_SCHEME_RE.test(value.slice(0, schemeEnd))) return false;

  let remainder = value.slice(schemeEnd + 1);
  const fragmentIndex = remainder.indexOf("#");
  if (fragmentIndex !== -1) {
    if (remainder.indexOf("#", fragmentIndex + 1) !== -1) return false;
    if (!RFC3986_QUERY_OR_FRAGMENT_RE.test(remainder.slice(fragmentIndex + 1))) return false;
    remainder = remainder.slice(0, fragmentIndex);
  }

  const queryIndex = remainder.indexOf("?");
  if (queryIndex !== -1) {
    if (!RFC3986_QUERY_OR_FRAGMENT_RE.test(remainder.slice(queryIndex + 1))) return false;
    remainder = remainder.slice(0, queryIndex);
  }
  return validateRfc3986HierPart(remainder);
}

module.exports = Object.freeze({
  "date-time": validateRfc3339DateTime,
  uri: validateAbsoluteRfc3986Uri,
});
