import { Link } from 'react-router-dom'
import { EmptyState } from '../components/ui'

export default function NotFoundPage() {
  return (
    <div className="py-10">
      <EmptyState
        icon="?"
        title="Page not found"
        description="That route does not exist in TraceLens."
        action={
          <Link to="/overview" className="btn btn-primary">
            Back to overview
          </Link>
        }
      />
    </div>
  )
}
