// 시각은 브라우저 시간대와 상관없이 항상 한국 시간(Asia/Seoul)으로 보여 준다.
const TIME_ZONE = 'Asia/Seoul'

const formatter = new Intl.DateTimeFormat('ko-KR', {
  timeZone: TIME_ZONE,
  month: 'numeric',
  day: 'numeric',
  weekday: 'short',
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
})

interface Parts {
  month: string
  day: string
  weekday: string
  period: string
  hour: string
  minute: string
  /** 같은 날인지 비교하는 키 */
  dayKey: string
}

function partsOf(iso: string): Parts {
  const raw = Object.fromEntries(
    formatter
      .formatToParts(new Date(iso))
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value]),
  )
  return {
    month: raw.month,
    day: raw.day,
    weekday: raw.weekday,
    period: raw.dayPeriod,
    hour: raw.hour,
    minute: raw.minute,
    dayKey: `${raw.month}/${raw.day}`,
  }
}

function clock(p: Parts, minutes: 'auto' | 'always'): string {
  const showMinutes = minutes === 'always' || p.minute !== '00'
  return showMinutes ? `${p.period} ${p.hour}:${p.minute}` : `${p.period} ${p.hour}시`
}

/** 작업 시각: "10/30(금) 오후 8시", 정각이 아니면 "10/30(금) 오후 2:30". */
export function formatWhen(iso: string): string {
  const p = partsOf(iso)
  return `${p.month}/${p.day}(${p.weekday}) ${clock(p, 'auto')}`
}

/** 접수 시각: 분까지 항상 보여 준다. "10/29(목) 오후 4:02" */
export function formatSubmitted(iso: string): string {
  const p = partsOf(iso)
  return `${p.month}/${p.day}(${p.weekday}) ${clock(p, 'always')}`
}

/** 작업 구간: 같은 날이면 끝은 시각만 붙인다. "10/30(금) 오후 8시 ~ 9시" */
export function formatRange(startIso: string, endIso: string | null): string {
  if (!endIso) return formatWhen(startIso)
  const start = partsOf(startIso)
  const end = partsOf(endIso)
  if (start.dayKey !== end.dayKey) return `${formatWhen(startIso)} ~ ${formatWhen(endIso)}`
  const endText = end.period === start.period ? `${end.hour}${end.minute === '00' ? '시' : `:${end.minute}`}` : clock(end, 'auto')
  return `${formatWhen(startIso)} ~ ${endText}`
}

/** 이력 날짜: "2025-11-14" -> "2025년 11월" */
export function formatMonth(date: string): string {
  const [year, month] = date.split('-')
  return `${year}년 ${Number(month)}월`
}
