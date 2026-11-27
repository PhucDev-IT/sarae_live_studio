/** Saving source names does not require replacing an already connected OBS socket. */
export async function saveObsConnection<Input, Status extends { connected: boolean }>(input: Input, actions: {
  configure: (input: Input) => Promise<Status>
  connect: () => Promise<Status>
  configured: (status: Status) => void
  connecting: () => void
}): Promise<Status> {
  const saved = await actions.configure(input)
  actions.configured(saved)
  if (saved.connected) return saved
  actions.connecting()
  // Leave connection errors visible; the saved settings remain available for a retry.
  return actions.connect()
}
