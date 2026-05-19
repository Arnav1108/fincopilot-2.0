interface Props {
  node: string
}

export default function AgentStatus({ node }: Props) {
  return (
    <span className="text-xs text-muted-foreground mt-1">
      {node}<span className="animate-pulse">...</span>
    </span>
  )
}
