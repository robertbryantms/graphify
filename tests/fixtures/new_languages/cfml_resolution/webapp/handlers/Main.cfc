component extends="lib.Toolkit" {

	property name="cart" inject="cart";
	property name="invoice" inject="invoice@billing";
	property name="ghost" inject="ghost@billing";
	property name="audit" inject="logbox:root";

	function run() {
		var engine = new core.Engine();
		var ledger = createObject( "component", "services.Ledger" );
		var twin = createObject( "component", "Twin" );
		var fmt = createObject( "component", "lib.Formatter" );
		var gone = createObject( "component", "Missing" );
		include "/views/header.cfm";
	}

}
