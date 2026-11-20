component extends="Base" implements="IAuditable, ILoggable" accessors="true" {

	property name="stockService" inject="stockService@catalog";

	public any function index( event, rc, prc ) {
		var pricing = new helpers.Pricing();
		var svc = getInstance( "stockService" );
		refresh();
		this.refresh();
		include "partials/footer.cfm";
		return svc;
	}

	private void function refresh() {
		var order = createObject( "component", "Order" ).init();
	}

}
